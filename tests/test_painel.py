"""Testes do painel: link secreto, liga/desliga, configurações, lista e métricas."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import painel
import robo

TOKEN = "codigo-secreto-de-teste-123"
PIN = "DAN2026"
LINHA = "https://www.amazon.com.br/dp/AAAAAAAAAA | 10,00"


class WhatsAppFalso:
    def esta_pronto(self):
        return True

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


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    for nome, arquivo in {
        "ARQUIVO_LISTA": "lista.txt", "ARQUIVO_ESTADO": "estado.json", "ARQUIVO_CACHE": "pecas.json",
        "ARQUIVO_HISTORICO": "postados.log", "ARQUIVO_POSTS": "postados.jsonl",
        "ARQUIVO_CONFIG": "config.json", "ARQUIVO_POSTAR_AGORA": "postar_agora",
    }.items():
        monkeypatch.setattr(robo, nome, tmp_path / arquivo)
    monkeypatch.setattr(robo, "PASTA_PECAS", tmp_path / "output")
    (tmp_path / "output").mkdir()
    monkeypatch.setenv("PAINEL_TOKEN", TOKEN)
    monkeypatch.setenv("PAINEL_PIN", PIN)
    painel._erros_de_pin.clear()
    monkeypatch.setenv("WHATSAPP_CANAL", "Canal Teste")
    monkeypatch.setattr(painel, "whatsapp", WhatsAppFalso())
    monkeypatch.setitem(painel._cache_metricas, "dados", None)
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


def test_token_curto_demais_nao_abre_nada(cliente, monkeypatch):
    monkeypatch.setenv("PAINEL_TOKEN", "curto")
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


def test_trocar_o_pin_derruba_quem_estava_dentro(cliente, monkeypatch):
    monkeypatch.setenv("PAINEL_PIN", "NOVO123")
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
    assert robo.Config.carregar().minutos_entre_posts == 10


def test_config_invalida_e_recusada(cliente):
    resposta = cliente.post(url("api/config"), json={"minutos_entre_posts": 0, "hora_inicio": 9, "hora_fim": 22})
    assert resposta.status_code == 400


def test_lista_com_erro_nao_substitui_a_boa(cliente):
    resposta = cliente.post(url("api/lista"), json={"texto": "https://amzn.to/sem-preco"})
    assert resposta.status_code == 400
    assert robo.ARQUIVO_LISTA.read_text(encoding="utf-8").strip() == LINHA


def test_lista_valida_e_salva(cliente):
    nova = LINHA + "\nhttps://www.amazon.com.br/dp/BBBBBBBBBB | 20,00\n"
    assert cliente.post(url("api/lista"), json={"texto": nova}).json()["produtos"] == 2


def test_postar_agora_deixa_pedido_para_o_robo(cliente):
    cliente.post(url("api/postar-agora"))
    assert robo.ARQUIVO_POSTAR_AGORA.exists()


def test_metricas_cruzam_post_com_produto(cliente):
    robo.ARQUIVO_POSTS.write_text(json.dumps({
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
    (robo.PASTA_PECAS / "X_final.png").write_bytes(b"png")
    assert cliente.get(url("imagem/X_final.png")).status_code == 200
