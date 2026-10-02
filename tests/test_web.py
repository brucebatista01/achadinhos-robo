"""
Testes do app web com um pipeline FALSO.

O pipeline de verdade baixa da Amazon e chama IAs; aqui só queremos
testar o app: link secreto, validação do formulário, fila de trabalhos
e tratamento de erros.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pipeline import ETAPAS, Peca
from services.amazon_service import AmazonError
from web.app import Fabrica, criar_app

TOKEN = "token-de-teste"
BASE = f"/p/{TOKEN}"


class PipelineFalso:
    """Imita o Pipeline: avisa as 6 etapas e 'gera' uma imagem qualquer."""

    def __init__(self, pasta, erro: Exception | None = None):
        self._pasta = pasta
        self._erro = erro
        self.produtos = []

    def processar(self, produto, ao_avancar):
        self.produtos.append(produto)
        for numero, descricao in enumerate(ETAPAS, 1):
            ao_avancar(numero, descricao)
        if self._erro:
            raise self._erro
        imagem = self._pasta / "B000TESTE_final.png"
        Image.new("RGB", (40, 50), "orange").save(imagem)
        return Peca("B000TESTE", "Lanterna Teste", imagem, self._pasta / "m.txt", "MENSAGEM")


@pytest.fixture
def montar(tmp_path):
    """Cria um cliente de teste com o pipeline falso escolhido."""
    clientes = []

    def _montar(erro: Exception | None = None):
        falso = PipelineFalso(tmp_path, erro)
        fabrica = Fabrica(criar_pipeline=lambda: falso, pasta_pecas=tmp_path / "web")
        cliente = TestClient(criar_app(TOKEN, fabrica))
        cliente.__enter__()  # dispara o ciclo de vida (aquecer)
        clientes.append(cliente)
        return cliente, falso

    yield _montar
    for cliente in clientes:
        cliente.__exit__(None, None, None)


def esperar_terminar(cliente, id_trabalho, limite=5.0):
    fim = time.time() + limite
    while time.time() < fim:
        dados = cliente.get(f"{BASE}/api/pecas/{id_trabalho}").json()
        if dados["estado"] in ("pronto", "erro"):
            return dados
        time.sleep(0.02)
    raise AssertionError("o trabalho não terminou a tempo")


PEDIDO = {"link": "https://amzn.to/abc", "preco_por": "129,90", "preco_de": "199,90", "cupom": "luz10"}


# ---------------------------------------------------------------------- #
# Link secreto
# ---------------------------------------------------------------------- #

def test_pagina_abre_com_o_token_certo(montar):
    cliente, _ = montar()
    resposta = cliente.get(f"{BASE}/")
    assert resposta.status_code == 200
    assert "Robô de Achadinhos" in resposta.text


@pytest.mark.parametrize("caminho", ["/p/errado/", "/p/errado/api/pecas", "/", "/docs"])
def test_sem_o_token_certo_tudo_e_404(montar, caminho):
    cliente, _ = montar()
    assert cliente.get(caminho).status_code == 404


def test_estaticos_nao_deixam_sair_da_pasta(montar):
    cliente, _ = montar()
    assert cliente.get(f"{BASE}/static/app.js").status_code == 200
    assert cliente.get(f"{BASE}/static/..%2Fapp.py").status_code == 404


# ---------------------------------------------------------------------- #
# Formulário
# ---------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("mudanca", "trecho_do_erro"),
    [
        ({"link": "não é um link"}, "Cole o link"),
        ({"link": ""}, "Cole o link"),
        ({"preco_por": ""}, "preço POR"),
        ({"preco_por": "abc"}, "Preço inválido"),
        ({"preco_de": "0"}, "maior que zero"),
    ],
)
def test_formulario_invalido_responde_422_com_mensagem(montar, mudanca, trecho_do_erro):
    cliente, falso = montar()
    resposta = cliente.post(f"{BASE}/api/pecas", json={**PEDIDO, **mudanca})
    assert resposta.status_code == 422
    assert trecho_do_erro in resposta.json()["detail"]
    assert falso.produtos == []  # nada entrou na fila


# ---------------------------------------------------------------------- #
# Fila de trabalhos
# ---------------------------------------------------------------------- #

def test_gera_peca_do_inicio_ao_fim(montar):
    cliente, falso = montar()

    resposta = cliente.post(f"{BASE}/api/pecas", json=PEDIDO)
    assert resposta.status_code == 202
    dados = esperar_terminar(cliente, resposta.json()["id"])

    assert dados["estado"] == "pronto"
    assert dados["etapa"] == len(ETAPAS)
    assert dados["nome_produto"] == "Lanterna Teste"
    assert dados["mensagem"] == "MENSAGEM"
    # O formulário chega convertido para o pipeline.
    produto = falso.produtos[0]
    assert (produto.preco_por, produto.preco_de, produto.cupom) == (129.90, 199.90, "LUZ10")

    imagem = cliente.get(f"{BASE}/{dados['url_imagem']}")
    assert imagem.status_code == 200
    assert imagem.headers["content-type"] == "image/png"


def test_link_sem_https_e_completado(montar):
    cliente, falso = montar()
    id_trabalho = cliente.post(f"{BASE}/api/pecas", json={**PEDIDO, "link": "amzn.to/abc"}).json()["id"]
    esperar_terminar(cliente, id_trabalho)
    assert falso.produtos[0].link == "https://amzn.to/abc"


def test_lista_mostra_a_peca_mais_nova_primeiro(montar):
    cliente, _ = montar()
    primeiro = cliente.post(f"{BASE}/api/pecas", json=PEDIDO).json()["id"]
    esperar_terminar(cliente, primeiro)
    segundo = cliente.post(f"{BASE}/api/pecas", json=PEDIDO).json()["id"]
    esperar_terminar(cliente, segundo)

    ids = [t["id"] for t in cliente.get(f"{BASE}/api/pecas").json()]
    assert ids == [segundo, primeiro]


def test_erro_esperado_aparece_com_a_mensagem_do_servico(montar):
    cliente, _ = montar(erro=AmazonError("Produto indisponível"))
    id_trabalho = cliente.post(f"{BASE}/api/pecas", json=PEDIDO).json()["id"]
    dados = esperar_terminar(cliente, id_trabalho)
    assert dados["estado"] == "erro"
    assert dados["erro"] == "Produto indisponível"


def test_bug_inesperado_vira_mensagem_amigavel(montar):
    cliente, _ = montar(erro=ZeroDivisionError("detalhe interno"))
    id_trabalho = cliente.post(f"{BASE}/api/pecas", json=PEDIDO).json()["id"]
    dados = esperar_terminar(cliente, id_trabalho)
    assert dados["estado"] == "erro"
    assert "detalhe interno" not in dados["erro"]  # não vaza detalhe técnico


def test_peca_inexistente_e_404(montar):
    cliente, _ = montar()
    assert cliente.get(f"{BASE}/api/pecas/naoexiste").status_code == 404
