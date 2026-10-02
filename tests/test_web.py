"""
Testes do app web com um pipeline FALSO.

O pipeline de verdade baixa da Amazon e chama IAs; aqui só queremos
testar o app: link secreto, validação do formulário, fila de trabalhos
e tratamento de erros.
"""

from __future__ import annotations

import threading
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
        # Fechado = o pipeline "trava" no meio, para testar peça em andamento.
        self.liberado = threading.Event()
        self.liberado.set()
        self.avaliacao = (4.8, 100)

    def processar(self, produto, ao_avancar):
        self.produtos.append(produto)
        for numero, descricao in enumerate(ETAPAS, 1):
            ao_avancar(numero, descricao)
        self.liberado.wait(timeout=5)
        if self._erro:
            raise self._erro
        imagem = self._pasta / "B000TESTE_final.png"
        Image.new("RGB", (40, 50), "orange").save(imagem)
        (self._pasta / "B000TESTE_sem_fundo.png").write_bytes(b"x")
        mensagens = {o.loja: f"POST {o.loja.upper()}" for o in produto.ofertas}
        return Peca("B000TESTE", "Lanterna Teste", imagem, mensagens, self.avaliacao)


@pytest.fixture
def montar(tmp_path):
    """Cria um cliente de teste com o pipeline falso escolhido."""
    clientes = []

    def _montar(erro: Exception | None = None):
        falso = PipelineFalso(tmp_path, erro)
        fabrica = Fabrica(criar_pipeline=lambda: falso, pasta_pecas=tmp_path / "web",
                          pasta_saida=tmp_path)
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


def pedido(**ofertas) -> dict:
    """Monta o corpo do pedido; sem argumentos, só a oferta da Amazon."""
    return {
        "link_amazon": "https://amzn.to/abc",
        "ofertas": ofertas or {"amazon": {"preco_por": "129,90", "preco_de": "199,90", "cupom": "luz10"}},
    }


def enviar(cliente, corpo):
    return cliente.post(f"{BASE}/api/pecas", json=corpo)


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
    ("corpo", "trecho_do_erro"),
    [
        ({**pedido(), "link_amazon": "não é link"}, "link do produto na Amazon"),
        (pedido(amazon={"preco_por": ""}, shopee={}), "pelo menos uma loja"),
        (pedido(amazon={"preco_por": "abc"}), "Amazon: Preço inválido"),
        (pedido(shopee={"preco_por": "10"}), "Shopee: cole o seu link"),
        (pedido(magalu={"link": "https://magalu.com/x"}), "Magalu: informe o preço POR"),
        (pedido(mercadolivre={"link": "https://a.b/c", "preco_por": "10", "preco_de": "0"}),
         "Mercado Livre: Preço precisa ser maior que zero"),
        (pedido(aliexpress={"link": "https://a.b/c", "preco_por": "10"}), "Loja desconhecida"),
    ],
)
def test_formulario_invalido_responde_422_com_mensagem(montar, corpo, trecho_do_erro):
    cliente, falso = montar()
    resposta = enviar(cliente, corpo)
    assert resposta.status_code == 422
    assert trecho_do_erro in resposta.json()["detail"]
    assert falso.produtos == []  # nada entrou na fila


def test_link_sem_https_e_completado(montar):
    cliente, falso = montar()
    corpo = pedido(shopee={"link": "s.shopee.com.br/x", "preco_por": "10"})
    corpo["link_amazon"] = "amzn.to/abc"
    esperar_terminar(cliente, enviar(cliente, corpo).json()["id"])
    produto = falso.produtos[0]
    assert produto.link_amazon == "https://amzn.to/abc"
    assert produto.ofertas[0].link == "https://s.shopee.com.br/x"


def test_lojas_vazias_sao_ignoradas_e_a_ordem_e_fixa(montar):
    cliente, falso = montar()
    corpo = pedido(
        magalu={"link": "https://magalu.com/x", "preco_por": "30"},
        shopee={"link": "", "preco_por": "", "preco_de": "", "cupom": ""},  # vazia
        amazon={"preco_por": "10"},
    )
    esperar_terminar(cliente, enviar(cliente, corpo).json()["id"])
    ofertas = falso.produtos[0].ofertas
    assert [o.loja for o in ofertas] == ["amazon", "magalu"]
    # Amazon sem link colado: o pipeline monta o link com a etiqueta depois.
    assert ofertas[0].link is None


# ---------------------------------------------------------------------- #
# Fila de trabalhos
# ---------------------------------------------------------------------- #

def test_gera_um_post_por_loja(montar):
    cliente, falso = montar()
    corpo = pedido(
        amazon={"preco_por": "129,90", "preco_de": "199,90", "cupom": "luz10"},
        shopee={"link": "https://s.shopee.com.br/x", "preco_por": "119,90"},
    )
    resposta = enviar(cliente, corpo)
    assert resposta.status_code == 202
    dados = esperar_terminar(cliente, resposta.json()["id"])

    assert dados["estado"] == "pronto"
    assert dados["etapa"] == len(ETAPAS)
    assert dados["nome_produto"] == "Lanterna Teste"
    assert dados["mensagens"] == [
        {"loja": "amazon", "nome_loja": "Amazon", "texto": "POST AMAZON"},
        {"loja": "shopee", "nome_loja": "Shopee", "texto": "POST SHOPEE"},
    ]
    amazon = falso.produtos[0].ofertas[0]
    assert (amazon.preco_por, amazon.preco_de, amazon.cupom) == (129.90, 199.90, "LUZ10")

    imagem = cliente.get(f"{BASE}/{dados['url_imagem']}")
    assert imagem.status_code == 200
    assert imagem.headers["content-type"] == "image/png"


def test_lista_mostra_a_peca_mais_nova_primeiro(montar):
    cliente, _ = montar()
    primeiro = enviar(cliente, pedido()).json()["id"]
    esperar_terminar(cliente, primeiro)
    segundo = enviar(cliente, pedido()).json()["id"]
    esperar_terminar(cliente, segundo)

    ids = [t["id"] for t in cliente.get(f"{BASE}/api/pecas").json()]
    assert ids == [segundo, primeiro]


def test_refazer_gera_outra_peca_com_os_mesmos_dados(montar):
    cliente, falso = montar()
    primeiro = enviar(cliente, pedido()).json()["id"]
    esperar_terminar(cliente, primeiro)

    resposta = cliente.post(f"{BASE}/api/pecas/{primeiro}/refazer")
    assert resposta.status_code == 202
    assert resposta.json()["id"] != primeiro
    esperar_terminar(cliente, resposta.json()["id"])
    assert falso.produtos[1] == falso.produtos[0]


def test_erro_esperado_aparece_com_a_mensagem_do_servico(montar):
    cliente, _ = montar(erro=AmazonError("Produto indisponível"))
    dados = esperar_terminar(cliente, enviar(cliente, pedido()).json()["id"])
    assert dados["estado"] == "erro"
    assert dados["erro"] == "Produto indisponível"


def test_bug_inesperado_vira_mensagem_amigavel(montar):
    cliente, _ = montar(erro=ZeroDivisionError("detalhe interno"))
    dados = esperar_terminar(cliente, enviar(cliente, pedido()).json()["id"])
    assert dados["estado"] == "erro"
    assert "detalhe interno" not in dados["erro"]  # não vaza detalhe técnico


def test_peca_inexistente_e_404(montar):
    cliente, _ = montar()
    assert cliente.get(f"{BASE}/api/pecas/naoexiste").status_code == 404
    assert cliente.post(f"{BASE}/api/pecas/naoexiste/refazer").status_code == 404


# ---------------------------------------------------------------------- #
# Exclusão de peças
# ---------------------------------------------------------------------- #

def arquivos_do_produto(pasta):
    return sorted(p.name for p in pasta.glob("B000TESTE_*"))


def test_excluir_apaga_a_peca_e_os_arquivos(montar, tmp_path):
    cliente, _ = montar()
    id_trabalho = enviar(cliente, pedido()).json()["id"]
    esperar_terminar(cliente, id_trabalho)
    assert arquivos_do_produto(tmp_path) == ["B000TESTE_final.png", "B000TESTE_sem_fundo.png"]
    assert len(list((tmp_path / "web").iterdir())) == 1

    assert cliente.delete(f"{BASE}/api/pecas/{id_trabalho}").status_code == 204

    assert cliente.get(f"{BASE}/api/pecas").json() == []
    assert cliente.get(f"{BASE}/api/pecas/{id_trabalho}/imagem").status_code == 404
    assert arquivos_do_produto(tmp_path) == []
    assert list((tmp_path / "web").iterdir()) == []


def test_excluir_uma_versao_mantem_os_arquivos_da_outra(montar, tmp_path):
    cliente, _ = montar()
    primeira = enviar(cliente, pedido()).json()["id"]
    esperar_terminar(cliente, primeira)
    segunda = enviar(cliente, pedido()).json()["id"]
    esperar_terminar(cliente, segunda)

    assert cliente.delete(f"{BASE}/api/pecas/{primeira}").status_code == 204

    # A segunda versão é do mesmo produto: os arquivos dele continuam.
    assert arquivos_do_produto(tmp_path) != []
    assert cliente.get(f"{BASE}/api/pecas/{segunda}/imagem").status_code == 200


def test_nao_exclui_peca_que_ainda_esta_sendo_gerada(montar):
    cliente, falso = montar()
    falso.liberado.clear()
    id_trabalho = enviar(cliente, pedido()).json()["id"]

    resposta = cliente.delete(f"{BASE}/api/pecas/{id_trabalho}")
    assert resposta.status_code == 409
    assert "sendo gerada" in resposta.json()["detail"]

    falso.liberado.set()
    esperar_terminar(cliente, id_trabalho)
    assert cliente.delete(f"{BASE}/api/pecas/{id_trabalho}").status_code == 204


def test_excluir_peca_inexistente_e_404(montar):
    cliente, _ = montar()
    assert cliente.delete(f"{BASE}/api/pecas/naoexiste").status_code == 404


def test_limite_do_historico_tambem_apaga_os_arquivos(montar, tmp_path, monkeypatch):
    import web.app as modulo
    monkeypatch.setattr(modulo, "LIMITE_HISTORICO", 1)
    cliente, _ = montar()
    primeira = enviar(cliente, pedido()).json()["id"]
    esperar_terminar(cliente, primeira)
    segunda = enviar(cliente, pedido()).json()["id"]
    esperar_terminar(cliente, segunda)

    ids = [t["id"] for t in cliente.get(f"{BASE}/api/pecas").json()]
    assert ids == [segunda]
    # Só sobra a imagem da peça que ficou no histórico.
    assert [p.name for p in (tmp_path / "web").iterdir()] == [f"{segunda}.png"]


def test_peca_informa_a_avaliacao_usada_no_selo(montar):
    cliente, falso = montar()
    dados = esperar_terminar(cliente, enviar(cliente, pedido()).json()["id"])
    assert dados["avaliacao"] == [4.8, 100]

    falso.avaliacao = None  # produto novo, sem avaliações na Amazon
    dados = esperar_terminar(cliente, enviar(cliente, pedido()).json()["id"])
    assert dados["avaliacao"] is None
