"""
Serviço responsável pelo processamento de imagens dos produtos.

Arquitetura em camadas:
  Camada 1 - Remoção de fundo (rembg, local, gratuita).
  Camada 2 - Ambientação por IA (PhotoRoom, sandbox gratuito / live pago).

Fluxo completo: baixar imagem -> remover fundo -> ambientar via IA -> salvar.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import numpy as np
import onnxruntime
import requests
from dotenv import load_dotenv
from PIL import Image
from rembg import new_session, remove
from scipy.ndimage import binary_fill_holes

# Carrega as variáveis do arquivo .env (as chaves de API).
load_dotenv()


class ImageError(Exception):
    """Exceção específica do serviço de imagem, para tratamento isolado no main."""


class ImageService:
    _PHOTOROOM_URL = "https://image-api.photoroom.com/v2/edit"
    # Modelo mais fotorrealista para os fundos ambientalizados.
    _PHOTOROOM_MODELO = "background-studio-beta-2025-03-17"

    # Modelo de recorte. Medido em 01/10/2026 com 3 produtos reais:
    #   bria-rmbg          ~8 GB de RAM, ~25 s por foto
    #   isnet-general-use  ~1 GB de RAM,  ~2 s por foto, bordas quase iguais
    # O isnet é o padrão porque cabe numa VPS barata. Numa máquina forte,
    # dá para voltar ao bria com MODELO_RECORTE=bria-rmbg no .env.
    _MODELO_RECORTE_PADRAO = "isnet-general-use"

    _HEADERS_PADRAO = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    }

    def __init__(self, pasta_saida: str = "output", timeout: int = 60) -> None:
        self._timeout = timeout
        self._pasta_saida = Path(pasta_saida)
        # Garante que a pasta de saída exista (idempotente).
        self._pasta_saida.mkdir(parents=True, exist_ok=True)
        self._modelo_recorte = os.getenv("MODELO_RECORTE", self._MODELO_RECORTE_PADRAO)
        # Sessão do rembg (o modelo carregado na memória). Criada só no
        # primeiro uso e reaproveitada: sem isso, cada produto do lote
        # recarregaria o modelo do disco.
        self._sessao_rembg = None

    def preparar_modelo(self) -> None:
        """
        Carrega o modelo de recorte (e baixa, se for a primeira vez).
        O Dockerfile chama isto no build para o modelo já vir na imagem.
        """
        if self._sessao_rembg is None:
            opcoes = onnxruntime.SessionOptions()
            # Sem o "arena" o onnxruntime devolve a memória depois de cada
            # foto, em vez de guardar uma reserva grande: o uso fica estável.
            opcoes.enable_cpu_mem_arena = False
            self._sessao_rembg = new_session(self._modelo_recorte, sess_opts=opcoes)

    def baixar_imagem(self, url_imagem: str) -> bytes:
        """Baixa os bytes brutos de uma imagem a partir da URL."""
        try:
            resposta = requests.get(
                url_imagem, headers=self._HEADERS_PADRAO, timeout=self._timeout
            )
            resposta.raise_for_status()
        except requests.RequestException as erro:
            raise ImageError(f"Falha ao baixar imagem: {erro}") from erro
        return resposta.content

    # Fração mínima da borda que precisa ser branca para a foto ter um
    # "fundo de estúdio" a remover. Medido em 02/10/2026: produtos comuns
    # da Amazon ficam entre 0,82 e 0,97; capas de livro que ocupam a foto
    # inteira ficam em 0,0.
    _BORDA_BRANCA_MINIMA = 0.5

    @staticmethod
    def fracao_borda_branca(imagem: Image.Image, faixa: int = 6, limiar: int = 240) -> float:
        """Quanto da borda da foto (0 a 1) é praticamente branco."""
        pixels = np.asarray(imagem.convert("RGB"))
        borda = np.concatenate([
            pixels[:faixa].reshape(-1, 3), pixels[-faixa:].reshape(-1, 3),
            pixels[:, :faixa].reshape(-1, 3), pixels[:, -faixa:].reshape(-1, 3),
        ])
        return float((borda.min(axis=1) >= limiar).mean())

    def remover_fundo(self, imagem_bytes: bytes) -> Image.Image:
        """
        Remove o fundo da imagem usando IA local (rembg).

        Recebe os bytes da imagem original e retorna um objeto de imagem
        Pillow em modo RGBA (com canal de transparência).

        Dois cuidados, descobertos com capas de livro:
        1. Foto SEM fundo branco (a capa ocupa a foto inteira) não tem o que
           recortar: a IA apagava as partes escuras da própria capa. Nesse
           caso a foto inteira é o produto.
        2. A IA às vezes deixa o produto semitransparente (80% dos pixels de
           um box de livros saíam assim). O pós-processamento do rembg deixa
           a máscara sólida, e "preencher buracos" fecha áreas internas que
           ela confundiu com fundo (ex.: partes brancas de uma capa).
        """
        try:
            original = Image.open(io.BytesIO(imagem_bytes)).convert("RGBA")
            if self.fracao_borda_branca(original) < self._BORDA_BRANCA_MINIMA:
                return original

            self.preparar_modelo()
            resultado = remove(imagem_bytes, session=self._sessao_rembg, post_process_mask=True)
            imagem = Image.open(io.BytesIO(resultado)).convert("RGBA")
        except Exception as erro:
            raise ImageError(f"Falha ao remover o fundo: {erro}") from erro
        return self._preencher_buracos(imagem)

    @staticmethod
    def _preencher_buracos(imagem: Image.Image) -> Image.Image:
        """Torna opaca toda área transparente cercada pelo produto."""
        alfa = np.asarray(imagem.getchannel("A"))
        solido = binary_fill_holes(alfa >= 128)
        alfa_novo = np.where(solido & (alfa < 128), 255, alfa).astype("uint8")
        resultado = imagem.copy()
        resultado.putalpha(Image.fromarray(alfa_novo))
        return resultado

    def enquadrar_produto(
        self,
        imagem: Image.Image,
        tamanho: tuple[int, int] = (1080, 1350),
        altura_relativa: float = 0.58,
        largura_maxima_relativa: float = 0.80,
    ) -> Image.Image:
        """
        Cola o produto recortado numa tela transparente maior, deixando margem.

        Por quê: as fotos da Amazon têm o produto ocupando quase todo o
        quadro. Com 'referenceBox=originalImage', o PhotoRoom preserva esse
        enquadramento e não sobra espaço para o cenário (CONTEXTO.md, 6.1).
        Resolver o enquadramento aqui, com Pillow (local e determinístico),
        deixa o PhotoRoom responsável só pelo que ele faz bem: o cenário.

        O tamanho padrão 1080x1350 (4:5) é o formato vertical que mais ocupa
        a tela do celular no WhatsApp.
        """
        if imagem.mode != "RGBA":
            imagem = imagem.convert("RGBA")

        # Corta a área transparente em volta: medimos o produto, não a foto.
        caixa = imagem.getchannel("A").getbbox()
        if caixa is None:
            raise ImageError("A imagem está totalmente transparente (recorte falhou?).")
        produto = imagem.crop(caixa)

        largura_tela, altura_tela = tamanho
        # Escala pela altura, mas sem deixar produtos largos (ex.: teclado)
        # encostarem nas laterais.
        escala = min(
            altura_tela * altura_relativa / produto.height,
            largura_tela * largura_maxima_relativa / produto.width,
        )
        novo_tamanho = (round(produto.width * escala), round(produto.height * escala))
        produto = produto.resize(novo_tamanho, Image.Resampling.LANCZOS)

        tela = Image.new("RGBA", tamanho, (0, 0, 0, 0))
        posicao = (
            (largura_tela - produto.width) // 2,
            (altura_tela - produto.height) // 2,
        )
        tela.paste(produto, posicao, mask=produto)
        return tela

    def ambientar_com_ia(
        self, imagem: Image.Image, prompt: str, usar_sandbox: bool = True
    ) -> Image.Image:
        """
        Gera um fundo ambientalizado via IA usando a API do PhotoRoom.

        Recebe uma imagem Pillow (idealmente já sem fundo), envia ao
        PhotoRoom com uma descrição textual do cenário desejado (prompt),
        e retorna a imagem ambientalizada.

        usar_sandbox=True usa a chave gratuita (saída com marca d'água).
        """
        # Escolhe a chave conforme o modo (sandbox = grátis, live = pago).
        nome_var = "PHOTOROOM_SANDBOX_KEY" if usar_sandbox else "PHOTOROOM_LIVE_KEY"
        api_key = os.getenv(nome_var)
        if not api_key:
            raise ImageError(
                f"Chave de API não encontrada na variável {nome_var} (verifique o .env)."
            )

        # Converte a imagem Pillow de volta para bytes PNG, para o upload.
        buffer = io.BytesIO()
        imagem.save(buffer, format="PNG")
        buffer.seek(0)

        headers = {
            "x-api-key": api_key,
            "pr-ai-background-model-version": self._PHOTOROOM_MODELO,
        }
        # 'referenceBox=originalImage' mantém o enquadramento do produto.
        dados = {
            "background.prompt": prompt,
            # Âncora no produto original (posicionamento consistente).
            "referenceBox": "originalImage",
        }
        arquivos = {"imageFile": ("produto.png", buffer, "image/png")}

        try:
            resposta = requests.post(
                self._PHOTOROOM_URL,
                headers=headers,
                data=dados,
                files=arquivos,
                timeout=self._timeout,
            )
            resposta.raise_for_status()
        except requests.RequestException as erro:
            # Tenta extrair a mensagem de erro da API para facilitar o debug.
            detalhe = getattr(erro.response, "text", "") if hasattr(erro, "response") else ""
            raise ImageError(f"Falha na ambientação (PhotoRoom): {erro} | {detalhe}") from erro

        try:
            imagem_final = Image.open(io.BytesIO(resposta.content)).convert("RGBA")
        except Exception as erro:
            raise ImageError(f"Resposta do PhotoRoom não é uma imagem válida: {erro}") from erro

        return imagem_final

    @staticmethod
    def preservar_produto(ambientada: Image.Image, enquadrada: Image.Image) -> Image.Image:
        """
        Cola o produto ORIGINAL por cima do cenário gerado pela IA.

        O PhotoRoom redesenha o produto junto com o cenário e, em produtos
        com texto (capas de livro, embalagens), troca letras e cores. Como
        mandamos o produto já enquadrado (ver enquadrar_produto) e pedimos
        o mesmo enquadramento de volta, ele está exatamente no mesmo lugar
        nas duas imagens: colar o original garante a capa fiel, e o cenário
        (e as sombras em volta) continuam vindo da IA.
        """
        if ambientada.size != enquadrada.size:
            # Não deveria acontecer; se acontecer, ajusta em vez de falhar.
            enquadrada = enquadrada.resize(ambientada.size, Image.Resampling.LANCZOS)
        resultado = ambientada.convert("RGBA")
        resultado.alpha_composite(enquadrada.convert("RGBA"))
        return resultado

    def salvar_png(self, imagem: Image.Image, nome_arquivo: str) -> Path:
        """Salva a imagem como PNG (preserva a transparência) na pasta de saída."""
        if not nome_arquivo.lower().endswith(".png"):
            nome_arquivo += ".png"
        caminho = self._pasta_saida / nome_arquivo
        try:
            imagem.save(caminho, format="PNG")
        except OSError as erro:
            raise ImageError(f"Falha ao salvar PNG: {erro}") from erro
        return caminho

    def processar_url(self, url_imagem: str, nome_saida: str) -> Path:
        """
        Fluxo da camada 1 apenas: baixar -> remover fundo -> salvar PNG.

        Método de conveniência que orquestra as etapas em uma só chamada.
        """
        imagem_bytes = self.baixar_imagem(url_imagem)
        imagem_sem_fundo = self.remover_fundo(imagem_bytes)
        return self.salvar_png(imagem_sem_fundo, nome_saida)


# Bloco de teste rápido: fluxo completo (remove fundo -> ambienta -> salva).
if __name__ == "__main__":
    servico = ImageService()
    url = input("Cole a URL de uma imagem de produto: ").strip()
    prompt = input("Descreva o cenário desejado (ou Enter para um padrão): ").strip()
    if not prompt:
        prompt = (
            "the product on a clean marble bathroom countertop, soft natural "
            "lighting, spa atmosphere, blurred background, photorealistic"
        )

    try:
        print("\n1) Baixando a imagem...")
        imagem_bytes = servico.baixar_imagem(url)

        print("2) Removendo o fundo (rembg, local)...")
        sem_fundo = servico.remover_fundo(imagem_bytes)

        print("3) Ambientando via IA (PhotoRoom sandbox)... pode levar alguns segundos.")
        ambientada = servico.ambientar_com_ia(sem_fundo, prompt, usar_sandbox=True)

        caminho = servico.salvar_png(ambientada, "produto_ambientado")
        print(f"\n✓ Imagem ambientalizada salva em: {caminho.resolve()}")
        print("  (Terá marca d'água por ser modo sandbox — isso é esperado.)")

    except ImageError as erro:
        print(f"[ERRO] {erro}")