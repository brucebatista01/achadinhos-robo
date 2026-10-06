"""Testes do robô automático, sem rede, sem WhatsApp e sem gastar crédito."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

import robo
from pipeline import AmazonError, Peca
from services.whatsapp_service import WhatsAppError


@pytest.fixture(autouse=True)
def pasta_dados(tmp_path, monkeypatch):
    """Cada teste usa uma pasta de dados própria e vazia."""
    monkeypatch.setattr(robo, "ARQUIVO_LISTA", tmp_path / "lista.txt")
    monkeypatch.setattr(robo, "ARQUIVO_ESTADO", tmp_path / "estado.json")
    monkeypatch.setattr(robo, "ARQUIVO_CACHE", tmp_path / "pecas.json")
    monkeypatch.setattr(robo, "ARQUIVO_HISTORICO", tmp_path / "postados.log")
    monkeypatch.setattr(robo, "ARQUIVO_POSTS", tmp_path / "postados.jsonl")
    monkeypatch.setattr(robo, "ARQUIVO_CONFIG", tmp_path / "config.json")
    monkeypatch.setattr(robo, "ARQUIVO_POSTAR_AGORA", tmp_path / "postar_agora")
    return tmp_path


class WhatsAppFalso:
    def __init__(self, falhar: bool = False) -> None:
        self.posts: list[tuple[str, Path, str]] = []
        self.avisos: list[str] = []
        self.falhar = falhar

    def postar(self, canal, imagem, texto):
        if self.falhar:
            raise WhatsAppError("sem conexão")
        self.posts.append((canal, imagem, texto))
        return {"etiquetaIA": True, "serverId": len(self.posts)}

    def avisar(self, texto):
        self.avisos.append(texto)


class PipelineFalso:
    def __init__(self, pasta: Path, erro: Exception | None = None) -> None:
        self.pasta, self.erro, self.chamadas = pasta, erro, 0

    def processar(self, produto, ao_avancar=None):
        self.chamadas += 1
        if self.erro:
            raise self.erro
        imagem = self.pasta / f"peca{self.chamadas}.png"
        imagem.write_bytes(b"png")
        return Peca("AAAAAAAAAA", "Produto", imagem, {"amazon": TEXTO_PECA})


LINHA_A = "https://www.amazon.com.br/dp/AAAAAAAAAA | 10,00"
TEXTO_PECA = ("CHAMADA BOA\n\n✅ Produto\n\n🔥 POR R$ 10,00\n\n"
              "🔗 https://www.amazon.com.br/dp/AAAAAAAAAA?tag=bru2001-20")
LINHA_B = "https://www.amazon.com.br/dp/BBBBBBBBBB | 20,00 | 30,00 | CUPOM"


def escrever_lista(pasta: Path, *linhas: str) -> None:
    (pasta / "lista.txt").write_text("# comentário\n\n" + "\n".join(linhas), encoding="utf-8")


def criar_robo(pasta, whatsapp=None, erro=None, preco=(10.0, None)) -> robo.Robo:
    instancia = robo.Robo(canal="Canal Teste", whatsapp=whatsapp or WhatsAppFalso(),
                          buscar_preco=lambda asin: preco)
    instancia._pipeline = PipelineFalso(pasta, erro)
    return instancia


# ---------------------------------------------------------------- lista


def test_le_lista_ignorando_comentarios(pasta_dados):
    escrever_lista(pasta_dados, LINHA_A, LINHA_B)
    linhas = [linha for linha, _ in robo.ler_lista()]
    assert linhas == [LINHA_A, LINHA_B]


def test_linha_errada_para_o_robo(pasta_dados):
    escrever_lista(pasta_dados, LINHA_A, "https://amzn.to/x")  # sem preço
    with pytest.raises(robo.RoboError, match="Linha 4"):
        robo.ler_lista()


def test_lista_inexistente_ou_vazia(pasta_dados):
    with pytest.raises(robo.RoboError, match="não encontrada"):
        robo.ler_lista()
    escrever_lista(pasta_dados)
    with pytest.raises(robo.RoboError, match="vazia"):
        robo.ler_lista()


# ---------------------------------------------------------------- sorteio


def test_sorteio_nunca_repete_o_ultimo(pasta_dados):
    escrever_lista(pasta_dados, LINHA_A, LINHA_B)
    produtos = robo.ler_lista()
    sorteio = random.Random(0)
    for _ in range(50):
        linha, _ = robo.sortear(produtos, LINHA_A, sorteio)
        assert linha == LINHA_B


def test_sorteio_com_um_produto_so_repete(pasta_dados):
    escrever_lista(pasta_dados, LINHA_A)
    assert robo.sortear(robo.ler_lista(), LINHA_A)[0] == LINHA_A


def test_horario_de_postagem():
    from datetime import datetime
    assert robo.dentro_do_horario(datetime(2026, 10, 5, 8, 0))
    assert robo.dentro_do_horario(datetime(2026, 10, 5, 22, 59))
    assert not robo.dentro_do_horario(datetime(2026, 10, 5, 23, 0))
    assert not robo.dentro_do_horario(datetime(2026, 10, 5, 3, 0))


# ---------------------------------------------------------------- postagem


def test_posta_registra_e_guarda_o_ultimo(pasta_dados):
    escrever_lista(pasta_dados, LINHA_A)
    whatsapp = WhatsAppFalso()
    criar_robo(pasta_dados, whatsapp).postar_um(robo.Estado())

    canal, imagem, texto = whatsapp.posts[0]
    assert (canal, texto) == ("Canal Teste", TEXTO_PECA)
    assert imagem.exists()
    assert robo.Estado.carregar().ultimo == LINHA_A
    assert LINHA_A in (pasta_dados / "postados.log").read_text(encoding="utf-8")


def test_reaproveita_a_peca_do_mesmo_produto(pasta_dados):
    escrever_lista(pasta_dados, LINHA_A)
    instancia = criar_robo(pasta_dados)
    instancia.postar_um(robo.Estado())
    instancia.postar_um(robo.Estado())
    assert instancia._pipeline.chamadas == 1  # gerou uma vez, postou duas


def test_link_que_falha_desliga_e_avisa(pasta_dados):
    escrever_lista(pasta_dados, LINHA_A)
    whatsapp = WhatsAppFalso()
    instancia = criar_robo(pasta_dados, whatsapp, erro=AmazonError("página não encontrada"))
    estado = robo.Estado()

    with pytest.raises(robo.RoboError) as erro:
        instancia.postar_um(estado)
    instancia.desligar_e_avisar(estado, str(erro.value))

    assert not robo.Estado.carregar().ligado
    assert "página não encontrada" in robo.Estado.carregar().motivo
    assert LINHA_A in whatsapp.avisos[0]
    assert not whatsapp.posts


def test_falha_no_whatsapp_vira_erro_do_robo(pasta_dados):
    escrever_lista(pasta_dados, LINHA_A)
    with pytest.raises(robo.RoboError, match="WhatsApp"):
        criar_robo(pasta_dados, WhatsAppFalso(falhar=True)).postar_um(robo.Estado())
    assert not (pasta_dados / "postados.log").exists()


def test_desligar_durante_o_post_e_respeitado(pasta_dados):
    """Se alguém desliga enquanto a peça é gerada, o post não religa o robô."""
    escrever_lista(pasta_dados, LINHA_A)
    robo.Estado(ligado=False).salvar()
    criar_robo(pasta_dados).postar_um(robo.Estado(ligado=True))
    assert not robo.Estado.carregar().ligado


# ---------------------------------------------------------------- comandos


def test_comandos_ligar_desligar(pasta_dados, capsys):
    assert robo.main(["desligar"]) == 0
    assert not robo.Estado.carregar().ligado
    assert robo.main(["ligar"]) == 0
    assert robo.Estado.carregar().ligado
    robo.main(["status"])
    assert "Ligado" in capsys.readouterr().out


# ---------------------------------------------------------------- afiliado


def test_etiqueta_de_afiliado_substitui_o_link_da_lista(pasta_dados, monkeypatch):
    monkeypatch.setenv("AMAZON_TAG", "bru2001-20")
    escrever_lista(pasta_dados, "https://amzn.to/linkdeoutro | 10,00")
    _, produto = robo.ler_lista()[0]
    assert produto.ofertas[0].link is None  # o pipeline monta com a etiqueta
    assert produto.link_amazon == "https://amzn.to/linkdeoutro"  # foto continua vindo daqui


def test_sem_etiqueta_mantem_o_link_da_lista(pasta_dados, monkeypatch):
    monkeypatch.delenv("AMAZON_TAG", raising=False)
    escrever_lista(pasta_dados, LINHA_A)
    assert robo.ler_lista()[0][1].ofertas[0].link == LINHA_A.split(" |")[0]


# ---------------------------------------------------------------- painel


def test_post_guarda_detalhes_para_o_painel(pasta_dados):
    import json
    escrever_lista(pasta_dados, LINHA_A)
    criar_robo(pasta_dados).postar_um(robo.Estado())
    registro = json.loads((pasta_dados / "postados.jsonl").read_text(encoding="utf-8"))
    assert registro["asin"] == "AAAAAAAAAA"
    assert registro["server_id"] == 1
    assert registro["etiqueta_ia"] is True


def test_config_do_painel_vale_por_cima_do_env(pasta_dados):
    from datetime import datetime
    robo.Config(minutos_entre_posts=10, hora_inicio=9, hora_fim=21).salvar()
    config = robo.Config.carregar()
    assert (config.minutos_entre_posts, config.hora_inicio, config.hora_fim) == (10, 9, 21)
    assert not robo.dentro_do_horario(datetime(2026, 10, 5, 8, 30), config)


def test_config_invalida_nao_e_salva(pasta_dados):
    with pytest.raises(ValueError):
        robo.Config(minutos_entre_posts=0).salvar()
    with pytest.raises(ValueError):
        robo.Config(hora_inicio=22, hora_fim=8).salvar()
    assert not (pasta_dados / "config.json").exists()


# ---------------------------------------------------------------- preço do momento


def test_posta_com_o_preco_de_agora_da_amazon(pasta_dados):
    escrever_lista(pasta_dados, LINHA_A)
    whatsapp = WhatsAppFalso()
    criar_robo(pasta_dados, whatsapp, preco=(8.5, 12.0)).postar_um(robo.Estado())
    texto = whatsapp.posts[0][2]
    assert "🔥 DE R$ 12,00 | POR R$ 8,50" in texto
    assert texto.startswith("CHAMADA BOA")
    assert texto.endswith("?tag=bru2001-20")


def test_sem_preco_na_pagina_usa_o_da_lista(pasta_dados, monkeypatch):
    monkeypatch.setattr(robo.time, "sleep", lambda s: None)
    escrever_lista(pasta_dados, LINHA_A)
    whatsapp = WhatsAppFalso()
    criar_robo(pasta_dados, whatsapp, preco=(None, None)).postar_um(robo.Estado())
    assert whatsapp.posts[0][2] == TEXTO_PECA
