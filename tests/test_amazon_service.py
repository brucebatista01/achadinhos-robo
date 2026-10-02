"""Testes do AmazonService com HTML de exemplo (sem acessar a Amazon)."""

from __future__ import annotations

import pytest

from services.amazon_service import AmazonError, AmazonService


@pytest.fixture
def servico():
    return AmazonService()


@pytest.mark.parametrize(
    "link",
    [
        "https://www.amazon.com.br/dp/B09HW92VTZ",
        "https://www.amazon.com.br/Mascara-Siage/dp/B09HW92VTZ/ref=sr_1_1?tag=x-20",
        "https://www.amazon.com.br/gp/product/B09HW92VTZ?th=1",
    ],
)
def test_extrair_asin_de_links_variados(servico, link):
    assert servico.extrair_asin(link) == "B09HW92VTZ"


def test_extrair_asin_de_link_sem_produto_e_erro(servico):
    with pytest.raises(AmazonError):
        servico.extrair_asin("https://www.amazon.com.br/ofertas")


def test_resolver_link_com_asin_nao_faz_requisicao(servico, monkeypatch):
    def proibido(*args, **kwargs):
        raise AssertionError("não deveria acessar a rede")

    monkeypatch.setattr(servico._session, "get", proibido)
    link = "https://www.amazon.com.br/dp/B09HW92VTZ?tag=x"
    assert servico.resolver_link(link) == link


def test_extrair_imagem_principal_pelo_json_prefere_main(servico):
    html = (
        "'colorImages': { 'initial': A.$.parseJSON('["
        '{"hiRes":"https://m.media-amazon.com/images/I/outra.jpg","variant":"PT01"},'
        '{"hiRes":"https://m.media-amazon.com/images/I/principal.jpg","variant":"MAIN"}'
        "]')"
    )
    assert servico.extrair_imagem_principal(html).endswith("principal.jpg")


def test_extrair_imagem_principal_usa_fallback_sl1500(servico):
    html = '<img src="https://m.media-amazon.com/images/I/61WDd._AC_SL1500_.jpg">'
    assert "_SL1500_" in servico.extrair_imagem_principal(html)


def test_extrair_imagem_principal_sem_imagem_e_erro(servico):
    with pytest.raises(AmazonError):
        servico.extrair_imagem_principal("<html></html>")


def test_extrair_avaliacao(servico):
    html = (
        '<span id="acrPopover" class="x" title="4,8 de 5 estrelas">'
        '<span id="acrCustomerReviewText" class="a-size-base">(3.423)</span>'
    )
    assert servico.extrair_avaliacao(html) == (4.8, 3423)


def test_extrair_avaliacao_de_produto_sem_avaliacoes_retorna_none(servico):
    assert servico.extrair_avaliacao("<html></html>") is None
