"""
Testes de enquadramento (ImageService) e acabamento (CompositionService).

Usam imagens sintéticas criadas na hora: um retângulo colorido faz o papel
do produto recortado. Assim o teste não depende de foto nem de internet.
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageChops

from services.composition_service import CompositionError, CompositionService
from services.image_service import ImageError, ImageService


def produto_falso(largura: int, altura: int) -> Image.Image:
    """Imita um PNG sem fundo: margem transparente com o 'produto' no meio."""
    imagem = Image.new("RGBA", (largura + 100, altura + 100), (0, 0, 0, 0))
    imagem.paste((200, 50, 50, 255), (50, 50, 50 + largura, 50 + altura))
    return imagem


def area_alterada(antes: Image.Image, depois: Image.Image) -> tuple | None:
    """
    Caixa da região onde as cores mudaram (None = imagens iguais).

    Compara em RGB de propósito: em RGBA, o getbbox() do Pillow olha só o
    canal de transparência e ignoraria qualquer mudança de cor.
    """
    return ImageChops.difference(antes.convert("RGB"), depois.convert("RGB")).getbbox()


# ---------------------------------------------------------------------- #
# Enquadramento
# ---------------------------------------------------------------------- #

@pytest.fixture
def servico_imagem(tmp_path):
    return ImageService(pasta_saida=str(tmp_path))


def test_enquadrar_produto_alto_ocupa_58_por_cento_da_altura(servico_imagem):
    tela = servico_imagem.enquadrar_produto(produto_falso(300, 900))

    assert tela.size == (1080, 1350)
    x0, y0, x1, y1 = tela.getchannel("A").getbbox()
    assert (y1 - y0) == pytest.approx(1350 * 0.58, abs=2)
    # Centralizado na horizontal.
    assert x0 == pytest.approx(1080 - x1, abs=1)


def test_enquadrar_produto_largo_respeita_largura_maxima(servico_imagem):
    tela = servico_imagem.enquadrar_produto(produto_falso(2000, 400))

    x0, _, x1, _ = tela.getchannel("A").getbbox()
    assert (x1 - x0) <= 1080 * 0.80 + 1


def test_enquadrar_imagem_toda_transparente_e_erro(servico_imagem):
    vazia = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    with pytest.raises(ImageError):
        servico_imagem.enquadrar_produto(vazia)


# ---------------------------------------------------------------------- #
# Composição
# ---------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("quantidade", "esperado"),
    [(0, "0"), (151, "151"), (1000, "1mil"), (3423, "3,4mil"),
     (3499, "3,4mil"),  # trunca: nunca arredonda para cima
     (10000, "10mil"), (1_250_000, "1,2mi")],
)
def test_formatar_quantidade(quantidade, esperado):
    assert CompositionService.formatar_quantidade(quantidade) == esperado


def test_formatar_quantidade_negativa_e_erro():
    with pytest.raises(CompositionError):
        CompositionService.formatar_quantidade(-1)


@pytest.mark.parametrize(("entrada", "esperado"), [("canal", "@canal"), (" @canal ", "@canal")])
def test_arroba_e_normalizado(entrada, esperado):
    assert CompositionService(entrada)._arroba_canal == esperado


def test_compor_sem_selo_nem_arroba_nao_altera_a_imagem():
    base = Image.new("RGBA", (1080, 1350), (90, 120, 150, 255))
    resultado = CompositionService().compor(base)
    assert area_alterada(base, resultado) is None


def test_compor_com_selo_desenha_so_na_parte_de_baixo():
    base = Image.new("RGBA", (1080, 1350), (90, 120, 150, 255))
    resultado = CompositionService().compor(base, avaliacao=(4.8, 3423))

    alterado = area_alterada(base, resultado)
    assert alterado is not None
    _, topo_do_selo, _, _ = alterado
    assert topo_do_selo > 1350 * 0.8
    assert resultado.size == base.size


def test_compor_nao_modifica_a_imagem_original():
    base = Image.new("RGBA", (1080, 1350), (90, 120, 150, 255))
    copia = base.copy()
    CompositionService("@canal").compor(base, avaliacao=(4.8, 10))
    assert area_alterada(base, copia) is None
