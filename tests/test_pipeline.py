"""Testes das partes do pipeline que não dependem de rede."""

from __future__ import annotations

import pytest

from pipeline import Oferta, Pipeline, PipelineError
from services.amazon_service import AmazonService


def test_montar_link_afiliado_com_etiqueta():
    link = AmazonService().montar_link_afiliado("B0CNHBV6W5", " danilo-20 ")
    assert link == "https://www.amazon.com.br/dp/B0CNHBV6W5?tag=danilo-20"


@pytest.mark.parametrize("etiqueta", [None, "", "   "])
def test_montar_link_afiliado_sem_etiqueta_devolve_none(etiqueta):
    assert AmazonService().montar_link_afiliado("B0CNHBV6W5", etiqueta) is None


@pytest.fixture
def pipeline():
    # _link_da_oferta só usa o AmazonService: dá para testar sem criar o
    # pipeline de verdade (que exigiria chaves de API).
    p = Pipeline.__new__(Pipeline)
    p._amazon = AmazonService()
    return p


LINK_AMAZON = "https://amzn.to/abc"


def test_link_colado_pelo_operador_tem_prioridade(pipeline, monkeypatch):
    monkeypatch.setenv("AMAZON_TAG", "danilo-20")
    oferta = Oferta("amazon", 10.0, link="https://amzn.to/meu-link")
    assert pipeline._link_da_oferta(oferta, LINK_AMAZON, "B0CNHBV6W5") == "https://amzn.to/meu-link"


def test_amazon_sem_link_usa_a_etiqueta(pipeline, monkeypatch):
    monkeypatch.setenv("AMAZON_TAG", "danilo-20")
    link = pipeline._link_da_oferta(Oferta("amazon", 10.0), LINK_AMAZON, "B0CNHBV6W5")
    assert link == "https://www.amazon.com.br/dp/B0CNHBV6W5?tag=danilo-20"


def test_amazon_sem_link_e_sem_etiqueta_usa_o_link_original(pipeline, monkeypatch):
    monkeypatch.delenv("AMAZON_TAG", raising=False)
    assert pipeline._link_da_oferta(Oferta("amazon", 10.0), LINK_AMAZON, "X") == LINK_AMAZON


def test_outra_loja_sem_link_e_erro(pipeline):
    with pytest.raises(PipelineError, match="Shopee"):
        pipeline._link_da_oferta(Oferta("shopee", 10.0), LINK_AMAZON, "X")
