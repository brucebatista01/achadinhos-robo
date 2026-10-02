"""Testes da leitura do produtos.txt (a parte do main que não depende de rede)."""

from __future__ import annotations

import pytest

from main import (
    Produto,
    ProdutoInvalidoError,
    converter_preco,
    interpretar_linha,
    ler_produtos,
)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("169,97", 169.97),
        ("1.234,56", 1234.56),
        ("R$ 89,90", 89.90),
        ("169.97", 169.97),  # quem digita no padrão americano também funciona
        ("10", 10.0),
    ],
)
def test_converter_preco_aceita_formatos_comuns(texto, esperado):
    assert converter_preco(texto) == pytest.approx(esperado)


@pytest.mark.parametrize("texto", ["abc", "0", "-5", ""])
def test_converter_preco_rejeita_valores_invalidos(texto):
    with pytest.raises(ProdutoInvalidoError):
        converter_preco(texto)


def test_interpretar_linha_completa():
    produto = interpretar_linha("https://amzn.to/a | 169,97 | 278,65 | LUZ10")
    assert produto == Produto("https://amzn.to/a", 169.97, 278.65, "LUZ10")


def test_interpretar_linha_so_com_obrigatorios():
    produto = interpretar_linha("https://amzn.to/a | 10")
    assert produto == Produto("https://amzn.to/a", 10.0, None, None)


def test_interpretar_linha_com_campo_opcional_vazio():
    produto = interpretar_linha("https://amzn.to/a | 10 | | CUPOM")
    assert produto.preco_de is None
    assert produto.cupom == "CUPOM"


@pytest.mark.parametrize(
    "linha",
    ["https://amzn.to/a", "https://amzn.to/a |", "| 10", "a | 1 | 2 | 3 | 4"],
)
def test_interpretar_linha_rejeita_formato_errado(linha):
    with pytest.raises(ProdutoInvalidoError):
        interpretar_linha(linha)


def test_ler_produtos_ignora_comentarios_vazias_e_linhas_ruins(tmp_path):
    arquivo = tmp_path / "produtos.txt"
    arquivo.write_text(
        "# comentário\n"
        "\n"
        "https://a | 10\n"
        "linha sem preço\n"
        "   \n"
        "https://b | 5,50 | 9,90\n",
        encoding="utf-8",
    )

    produtos = ler_produtos(arquivo)

    # A linha ruim é pulada, mas NÃO derruba as outras.
    assert [p.link for p in produtos] == ["https://a", "https://b"]
    assert produtos[1].preco_de == pytest.approx(9.90)
