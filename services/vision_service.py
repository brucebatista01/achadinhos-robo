"""
Serviço de visão computacional (o "cérebro" do pipeline).

Recebe a imagem de um produto (idealmente já sem fundo), usa o modelo
de visão da Anthropic (Claude) para IDENTIFICAR o produto e DECIDIR o
cenário de ambientação ideal, retornando um prompt pronto para o PhotoRoom.

Isso torna o sistema universal: qualquer produto é analisado e recebe
um cenário adequado automaticamente, sem intervenção humana.
"""

from __future__ import annotations

import base64
import io
import json
import os

from anthropic import Anthropic, APIError
from dotenv import load_dotenv
from PIL import Image

load_dotenv()


class VisionError(Exception):
    """Exceção específica do serviço de visão, para tratamento isolado no main."""


class VisionService:
    # Haiku: rápido e barato, ideal para esta tarefa de classificação.
    _MODELO = "claude-haiku-4-5"

    # Instrução que ensina o Claude a agir como diretor de arte publicitário.
    _INSTRUCAO = (
        "Você é um diretor de arte de fotografia publicitária de e-commerce. "
        "Analise a imagem do produto e crie um cenário que gere DESEJO DE COMPRA.\n\n"
        "Responda APENAS com um objeto JSON válido, sem texto antes ou depois:\n"
        '{"produto": "nome curto", "categoria": "categoria geral", '
        '"prompt": "descrição do cenário em INGLÊS"}\n\n'
        "REGRAS para o campo 'prompt':\n"
        "1. Nomeie uma SUPERFÍCIE concreta e texturizada onde o produto repousa "
        "(ex.: rustic wooden table, white marble countertop, dark slate surface, "
        "woven linen cloth, brushed concrete).\n"
        "2. Nomeie o AMBIENTE ao redor (ex.: cozy bathroom, modern kitchen, "
        "sunlit bedroom, industrial workshop) — sempre desfocado ao fundo.\n"
        "3. Descreva a LUZ com temperatura (ex.: warm morning sunlight, "
        "soft diffused daylight, golden hour glow).\n"
        "4. Use no máximo UM objeto de apoio discreto, ou nenhum. "
        "NÃO espalhe vários objetos, cristais, líquidos ou flores.\n"
        "5. Termine com: 'blurred background, photorealistic, professional "
        "product photography'.\n"
        "6. Máximo 45 palavras. Não invente marcas nem textos.\n\n"
        "EXEMPLO de bom prompt: 'the product on a rustic wooden table in a cozy "
        "bathroom, warm morning sunlight from a window, a folded white towel "
        "nearby, blurred background, photorealistic, professional product "
        "photography'"
    )

    def __init__(self) -> None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise VisionError(
                "Chave ANTHROPIC_API_KEY não encontrada (verifique o .env)."
            )
        self._client = Anthropic(api_key=api_key)

    def _imagem_para_base64(self, imagem: Image.Image) -> str:
        """Converte uma imagem Pillow em string base64 (formato PNG)."""
        buffer = io.BytesIO()
        imagem.save(buffer, format="PNG")
        return base64.standard_b64encode(buffer.getvalue()).decode("utf-8")

    def gerar_cenario(self, imagem: Image.Image) -> dict:
        """
        Analisa a imagem do produto e retorna um dicionário com:
        produto, categoria e prompt de ambientação (em inglês).
        """
        imagem_b64 = self._imagem_para_base64(imagem)

        try:
            resposta = self._client.messages.create(
                model=self._MODELO,
                max_tokens=500,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": imagem_b64,
                                },
                            },
                            {"type": "text", "text": self._INSTRUCAO},
                        ],
                    }
                ],
            )
        except APIError as erro:
            raise VisionError(f"Falha na API da Anthropic: {erro}") from erro

        texto = resposta.content[0].text.strip()

        # Remove cercas de markdown, se o modelo as incluir por engano.
        if texto.startswith("```"):
            texto = texto.strip("`")
            if texto.startswith("json"):
                texto = texto[4:].strip()

        try:
            dados = json.loads(texto)
        except json.JSONDecodeError as erro:
            raise VisionError(
                f"A IA não retornou um JSON válido. Resposta: {texto!r}"
            ) from erro

        if "prompt" not in dados:
            raise VisionError(f"JSON sem o campo 'prompt'. Recebido: {dados}")

        return dados


# Bloco de teste rápido.
if __name__ == "__main__":
    caminho = input("Caminho do PNG sem fundo (ex: output/produto_sem_fundo.png): ").strip()
    try:
        imagem = Image.open(caminho)
        servico = VisionService()
        print("\nAnalisando o produto com a IA de visão...")
        resultado = servico.gerar_cenario(imagem)

        print("\n--- Cenário decidido pela IA ---")
        print(f"Produto:   {resultado.get('produto')}")
        print(f"Categoria: {resultado.get('categoria')}")
        print(f"\nPrompt gerado:\n{resultado.get('prompt')}")
    except FileNotFoundError:
        print(f"[ERRO] Arquivo não encontrado: {caminho}")
    except VisionError as erro:
        print(f"[ERRO] {erro}")