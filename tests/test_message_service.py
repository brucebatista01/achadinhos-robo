"""Testes do MessageService: formatação pura e headline com a IA simulada."""

from __future__ import annotations

import pytest

from services.message_service import MessageError, MessageService
from tests.conftest import ClienteAnthropicFalso


@pytest.fixture
def servico(chave_falsa):
    return MessageService()


# ---------------------------------------------------------------------- #
# Formatação (sem IA)
# ---------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        (0, "R$ 0,00"),
        (169.97, "R$ 169,97"),
        (1234.5, "R$ 1.234,50"),
        (1_000_000, "R$ 1.000.000,00"),
    ],
)
def test_formatar_preco_no_padrao_brasileiro(valor, esperado):
    assert MessageService.formatar_preco(valor) == esperado


def test_formatar_preco_negativo_e_erro():
    with pytest.raises(MessageError):
        MessageService.formatar_preco(-1)


def test_formatar_mensagem_completa():
    mensagem = MessageService.formatar_mensagem(
        "vira dia", "Lanterna X", "https://amzn.to/a", 169.97, 278.65, " luz10 "
    )
    assert mensagem == (
        "VIRA DIA\n\n"
        "✅ Lanterna X\n\n"
        "🔥 DE R$ 278,65 | POR R$ 169,97\n\n"
        "🎟️ CUPOM: LUZ10\n\n"
        "🔗 https://amzn.to/a"
    )


def test_formatar_mensagem_sem_opcionais_omite_as_linhas():
    mensagem = MessageService.formatar_mensagem("X", "Y", "https://a", 100)
    assert "CUPOM" not in mensagem
    assert "DE R$" not in mensagem
    assert "🔥 POR R$ 100,00" in mensagem


def test_formatar_mensagem_omite_preco_de_menor_que_preco_por():
    # "DE 90 POR 100" seria propaganda enganosa.
    mensagem = MessageService.formatar_mensagem("X", "Y", "https://a", 100, 90)
    assert "DE R$" not in mensagem


def test_formatar_mensagem_rejeita_link_sem_http():
    with pytest.raises(MessageError):
        MessageService.formatar_mensagem("X", "Y", "amzn.to/a", 100)


# ---------------------------------------------------------------------- #
# Headline (IA simulada)
# ---------------------------------------------------------------------- #

def test_gerar_headline_limpa_a_resposta_da_ia(servico):
    servico._client = ClienteAnthropicFalso('"Vira dia onde você apontar."\nExplicação extra')
    assert servico.gerar_headline("Lanterna", "Iluminação") == "VIRA DIA ONDE VOCÊ APONTAR"


def test_gerar_headline_envia_produto_e_categoria_no_prompt(servico):
    cliente = ClienteAnthropicFalso("Uma headline")
    servico._client = cliente
    servico.gerar_headline("Power Bank", "Eletrônicos")
    prompt = cliente.chamadas[0]["messages"][0]["content"]
    assert "Power Bank" in prompt and "Eletrônicos" in prompt


@pytest.mark.parametrize("resposta", ["", "   \n  ", "x" * 80])
def test_gerar_headline_rejeita_resposta_vazia_ou_longa(servico, resposta):
    servico._client = ClienteAnthropicFalso(resposta)
    with pytest.raises(MessageError):
        servico.gerar_headline("Produto", "Categoria")


def test_sem_chave_no_ambiente_da_erro_claro(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MessageError, match="ANTHROPIC_API_KEY"):
        MessageService()
