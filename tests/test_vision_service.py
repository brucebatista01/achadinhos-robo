"""Testes do VisionService com a IA simulada (sem gastar crédito)."""

from __future__ import annotations

import pytest
from PIL import Image

from services.vision_service import VisionError, VisionService
from tests.conftest import ClienteAnthropicFalso

IMAGEM = Image.new("RGBA", (10, 10), (255, 0, 0, 255))


@pytest.fixture
def servico(chave_falsa):
    return VisionService()


def test_gerar_cenario_le_o_json(servico):
    servico._client = ClienteAnthropicFalso(
        '{"produto": "Lanterna", "categoria": "Iluminação", "prompt": "on a table"}'
    )
    assert servico.gerar_cenario(IMAGEM)["prompt"] == "on a table"


def test_gerar_cenario_tolera_cerca_de_markdown(servico):
    servico._client = ClienteAnthropicFalso(
        '```json\n{"produto": "X", "categoria": "Y", "prompt": "Z"}\n```'
    )
    assert servico.gerar_cenario(IMAGEM)["produto"] == "X"


def test_gerar_cenario_envia_a_imagem_em_base64(servico):
    cliente = ClienteAnthropicFalso('{"prompt": "Z"}')
    servico._client = cliente
    servico.gerar_cenario(IMAGEM)
    bloco_imagem = cliente.chamadas[0]["messages"][0]["content"][0]
    assert bloco_imagem["type"] == "image"
    assert bloco_imagem["source"]["media_type"] == "image/png"


@pytest.mark.parametrize("resposta", ["não é json", '{"produto": "sem prompt"}'])
def test_gerar_cenario_com_resposta_invalida_e_erro(servico, resposta):
    servico._client = ClienteAnthropicFalso(resposta)
    with pytest.raises(VisionError):
        servico.gerar_cenario(IMAGEM)
