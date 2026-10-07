"""Testes do painel: link secreto, liga/desliga, configurações, lista e métricas."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import clientes
import painel
import robo
from clientes import Cliente

TOKEN = "codigo-secreto-de-teste-123"
PIN = "DAN2026"
LINHA = "https://www.amazon.com.br/dp/AAAAAAAAAA | 10,00"


class WhatsAppFalso:
    conectado = True
    desconectou = False

    def esta_pronto(self):
        return self.conectado

    def situacao(self):
        return {"pronto": self.conectado, "aguardandoPareamento": not self.conectado,
                "numero": "5511933771685" if self.conectado else None}

    def gerar_codigo(self, numero):
        if len(numero) < 10:
            from services.whatsapp_service import WhatsAppError
            raise WhatsAppError("Número inválido")
        return "ABCD1234"

    def desconectar(self):
        self.desconectou = True

    def metricas(self, canal, limite=60):
        return {
            "seguidores": 42,
            "posts": [
                {"serverId": 7, "quando": 1791240000, "legenda": "CHAMADA\n\n✅ Produto", "visualizacoes": 30,
                 "reacoes": 2, "encaminhamentos": 1},
                {"serverId": 8, "quando": 1791243600, "legenda": "OUTRO", "visualizacoes": 10,
                 "reacoes": 0, "encaminhamentos": 0},
            ],
        }


DONO: Cliente | None = None  # o cliente de teste da vez (para os testes lerem a pasta)
WPP = None


def criar_cliente(pasta, token=TOKEN, pin=PIN, id_="teste") -> Cliente:
    return Cliente(id=id_, nome="Teste", pasta=pasta, canal="Canal Teste", etiqueta="bru2001-20",
                   painel_token=token, painel_pin=pin, url_whatsapp="http://ponte-falsa")


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    global DONO, WPP
    DONO, WPP = criar_cliente(tmp_path), WhatsAppFalso()
    monkeypatch.setattr(clientes, "carregar", lambda: [DONO])
    monkeypatch.setattr(painel, "servico_whatsapp", lambda c: WPP)
    (tmp_path / "output").mkdir()
    painel._erros_de_pin.clear()
    painel._cache_metricas.clear()
    (tmp_path / "lista.txt").write_text(LINHA + "\n", encoding="utf-8")
    cliente = TestClient(painel.app)
    assert cliente.post(url("entrar"), json={"pin": PIN}).status_code == 200
    return cliente


def url(caminho: str) -> str:
    return f"/p/{TOKEN}/{caminho}"


def test_link_errado_parece_que_nao_existe(cliente):
    assert cliente.get("/p/errado/").status_code == 404
    assert cliente.get("/p/errado/api/resumo").status_code == 404
    assert cliente.post("/p/errado/api/ligar").status_code == 404


def test_token_curto_demais_nao_abre_nada(cliente, monkeypatch, tmp_path):
    monkeypatch.setattr(clientes, "carregar", lambda: [criar_cliente(tmp_path, token="curto")])
    assert cliente.get("/p/curto/").status_code == 404


def test_sem_pin_mostra_tela_de_pin_e_api_fechada(cliente):
    cliente.cookies.clear()
    assert "Digite o PIN" in cliente.get(url("")).text
    assert cliente.get(url("api/resumo")).status_code == 401
    assert cliente.post(url("api/ligar")).status_code == 401


def test_pin_errado_e_bloqueio(cliente):
    cliente.cookies.clear()
    for _ in range(painel.TENTATIVAS_MAXIMAS):
        assert cliente.post(url("entrar"), json={"pin": "ERRADO"}).status_code == 403
    # Bloqueado: nem o PIN certo entra até o tempo passar.
    assert cliente.post(url("entrar"), json={"pin": PIN}).status_code == 429


def test_pin_aceita_minusculas(cliente):
    cliente.cookies.clear()
    assert cliente.post(url("entrar"), json={"pin": "dan2026"}).status_code == 200
    assert cliente.get(url("api/resumo")).status_code == 200


def test_trocar_o_pin_derruba_quem_estava_dentro(cliente, monkeypatch, tmp_path):
    monkeypatch.setattr(clientes, "carregar", lambda: [criar_cliente(tmp_path, pin="NOVO123")])
    assert cliente.get(url("api/resumo")).status_code == 401


def test_pagina_abre_com_o_link_certo(cliente):
    resposta = cliente.get(url(""))
    assert resposta.status_code == 200
    assert "Robô de Achadinhos" in resposta.text


def test_ligar_e_desligar(cliente):
    cliente.post(url("api/desligar"))
    assert cliente.get(url("api/resumo")).json()["ligado"] is False
    cliente.post(url("api/ligar"))
    assert cliente.get(url("api/resumo")).json()["ligado"] is True


def test_salvar_config_valida(cliente):
    resposta = cliente.post(url("api/config"), json={"minutos_entre_posts": 10, "hora_inicio": 9, "hora_fim": 22})
    assert resposta.status_code == 200
    assert robo.Config.carregar(DONO).minutos_entre_posts == 10


def test_config_invalida_e_recusada(cliente):
    resposta = cliente.post(url("api/config"), json={"minutos_entre_posts": 0, "hora_inicio": 9, "hora_fim": 22})
    assert resposta.status_code == 400


def test_lista_com_erro_nao_substitui_a_boa(cliente):
    resposta = cliente.post(url("api/lista"), json={"texto": "https://amzn.to/sem-preco"})
    assert resposta.status_code == 400
    assert DONO.lista.read_text(encoding="utf-8").strip() == LINHA


def test_lista_valida_e_salva(cliente):
    nova = LINHA + "\nhttps://www.amazon.com.br/dp/BBBBBBBBBB | 20,00\n"
    assert cliente.post(url("api/lista"), json={"texto": nova}).json()["produtos"] == 2


def test_postar_agora_deixa_pedido_para_o_robo(cliente):
    cliente.post(url("api/postar-agora"))
    assert DONO.postar_agora.exists()


def test_metricas_cruzam_post_com_produto(cliente):
    DONO.posts.write_text(json.dumps({
        "quando": "2026-10-05T21:00:00-03:00", "linha": LINHA, "asin": "AAAAAAAAAA",
        "produto": "Pilha AA", "imagem": "AAAAAAAAAA_final.png", "server_id": 7,
    }) + "\n", encoding="utf-8")
    dados = cliente.get(url("api/metricas")).json()
    assert dados["seguidores"] == 42
    post = next(p for p in dados["posts"] if p["serverId"] == 7)
    assert post["produto"] == "Pilha AA"
    assert post["link"] == "https://www.amazon.com.br/dp/AAAAAAAAAA"
    assert dados["destaques"]["media_visualizacoes"] == 20
    assert dados["destaques"]["campeoes"][0] == 7


def test_imagem_nao_sai_da_pasta_das_pecas(cliente):
    assert cliente.get(url("imagem/..%2F.env")).status_code == 404
    (DONO.pecas / "X_final.png").write_bytes(b"png")
    assert cliente.get(url("imagem/X_final.png")).status_code == 200


# ---------------------------------------------------------------- conexão do WhatsApp


def test_situacao_do_whatsapp_conectado(cliente):
    dados = cliente.get(url("api/whatsapp")).json()
    assert dados["conectado"] is True
    assert dados["numero"] == "5511933771685"


def test_qr_aparece_quando_desconectado(cliente):
    WPP.conectado = False
    (DONO.qr).write_bytes(b"png")
    assert cliente.get(url("api/whatsapp")).json()["tem_qr"] is True
    assert cliente.get(url("qr.png")).content == b"png"


def test_qr_e_codigo_exigem_pin(cliente):
    cliente.cookies.clear()
    assert cliente.get(url("qr.png")).status_code == 401
    assert cliente.post(url("api/whatsapp/codigo"), json={"numero": "5511999998888"}).status_code == 401


def test_codigo_por_numero(cliente):
    assert cliente.post(url("api/whatsapp/codigo"), json={"numero": "5511999998888"}).json()["codigo"] == "ABCD1234"
    assert cliente.post(url("api/whatsapp/codigo"), json={"numero": "123"}).status_code == 400


def test_trocar_numero(cliente):
    assert cliente.post(url("api/whatsapp/desconectar")).status_code == 200
    assert WPP.desconectou


# ---------------------------------------------------------------- vários clientes


def test_cada_link_abre_o_painel_do_seu_cliente(tmp_path, monkeypatch):
    a = criar_cliente(tmp_path / "a", token="a" * 24, pin="1111", id_="a")
    b = criar_cliente(tmp_path / "b", token="b" * 24, pin="2222", id_="b")
    for c in (a, b):
        c.pecas.mkdir(parents=True)
    monkeypatch.setattr(clientes, "carregar", lambda: [a, b])
    monkeypatch.setattr(painel, "servico_whatsapp", lambda c: WhatsAppFalso())
    painel._erros_de_pin.clear()
    web = TestClient(painel.app)
    # O PIN de um não abre o painel do outro.
    assert web.post(f"/p/{'a' * 24}/entrar", json={"pin": "2222"}).status_code == 403
    assert web.post(f"/p/{'a' * 24}/entrar", json={"pin": "1111"}).status_code == 200
    web.post(f"/p/{'a' * 24}/api/desligar")
    assert not robo.Estado.carregar(a).ligado
    assert robo.Estado.carregar(b).ligado  # o outro cliente não mudou
    assert web.get(f"/p/{'b' * 24}/api/resumo").status_code == 401  # o crachá de A não serve em B
