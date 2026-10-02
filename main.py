"""
Orquestrador do robô de peças publicitárias.

Lê os produtos de `produtos.txt` e, para cada um, executa o pipeline:
extração -> remoção de fundo -> visão -> ambientação -> composição -> mensagem.
Salva a imagem e o texto prontos em `output/`.

O main NÃO sabe COMO cada etapa funciona: só sabe a ORDEM das etapas.
Cada serviço resolve o seu problema e avisa falhas pela própria exceção.
Isso permite trocar, por exemplo, o PhotoRoom por outra API mexendo em
um único arquivo.

Importante: o robô entrega a peça pronta e NÃO publica no WhatsApp
(decisão consciente, ver CONTEXTO.md, seção 8).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from services.amazon_service import AmazonError, AmazonService
from services.composition_service import CompositionError, CompositionService
from services.image_service import ImageError, ImageService
from services.message_service import MessageError, MessageService
from services.vision_service import VisionError, VisionService

load_dotenv()

ARQUIVO_PRODUTOS = Path("produtos.txt")
PASTA_SAIDA = Path("output")

# Pausa entre produtos: evita martelar a Amazon (que bloqueia robôs
# apressados) e as APIs pagas.
SEGUNDOS_ENTRE_PRODUTOS = 5

# Todas as falhas "esperadas" do pipeline. Qualquer outra exceção é bug
# de verdade e merece aparecer com o traceback completo.
ERROS_DO_PIPELINE = (
    AmazonError, ImageError, VisionError, CompositionError, MessageError
)

log = logging.getLogger("robo")


class ProdutoInvalidoError(ValueError):
    """Linha do produtos.txt mal formatada."""


@dataclass(frozen=True)
class Produto:
    """Uma linha do produtos.txt já interpretada."""

    link: str
    preco_por: float
    preco_de: float | None = None
    cupom: str | None = None


# ---------------------------------------------------------------------- #
# Leitura do produtos.txt
# ---------------------------------------------------------------------- #

def converter_preco(texto: str) -> float:
    """
    Converte preço no formato brasileiro para float.

    Aceita '169,97', '1.234,56', 'R$ 1.234,56' e também '169.97'.
    O Danilo vai digitar do jeito que estiver acostumado; o robô se adapta.
    """
    limpo = texto.replace("R$", "").strip()
    if "," in limpo:
        # Formato BR: ponto é milhar, vírgula é decimal.
        limpo = limpo.replace(".", "").replace(",", ".")
    try:
        valor = float(limpo)
    except ValueError as erro:
        raise ProdutoInvalidoError(f"Preço inválido: {texto!r}") from erro
    if valor <= 0:
        raise ProdutoInvalidoError(f"Preço precisa ser maior que zero: {texto!r}")
    return valor


def interpretar_linha(linha: str) -> Produto:
    """
    Formato: link | preco_por | preco_de | cupom
    Só link e preco_por são obrigatórios.
    """
    campos = [campo.strip() for campo in linha.split("|")]
    if len(campos) < 2 or not campos[0] or not campos[1]:
        raise ProdutoInvalidoError(
            "Faltou o preço. Use: link | preco_por | preco_de | cupom"
        )
    if len(campos) > 4:
        raise ProdutoInvalidoError("Campos demais (máximo 4, separados por '|').")

    campos += [""] * (4 - len(campos))  # completa os opcionais vazios
    link, preco_por, preco_de, cupom = campos

    return Produto(
        link=link,
        preco_por=converter_preco(preco_por),
        preco_de=converter_preco(preco_de) if preco_de else None,
        cupom=cupom or None,
    )


def ler_produtos(caminho: Path) -> list[Produto]:
    """
    Lê o arquivo de produtos, ignorando linhas vazias e comentários (#).

    Uma linha mal formatada é avisada e pulada, sem derrubar as outras.
    """
    produtos: list[Produto] = []
    for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), 1):
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        try:
            produtos.append(interpretar_linha(linha))
        except ProdutoInvalidoError as erro:
            log.warning("Linha %d ignorada: %s", numero, erro)
    return produtos


# ---------------------------------------------------------------------- #
# Pipeline de um produto
# ---------------------------------------------------------------------- #

class Robo:
    """
    Guarda os serviços e processa um produto por vez.

    Os serviços são criados UMA vez e reaproveitados no lote inteiro:
    abrir a conexão e validar as chaves para cada produto seria desperdício.
    """

    def __init__(self, pasta_saida: Path = PASTA_SAIDA) -> None:
        self._pasta_saida = pasta_saida
        self._amazon = AmazonService()
        self._imagem = ImageService(pasta_saida=str(pasta_saida))
        self._visao = VisionService()
        self._mensagem = MessageService()
        # O @ do canal não é segredo, mas fica no .env para o Danilo trocar
        # sem mexer no código. Sem ele, a peça sai sem marca d'água.
        self._composicao = CompositionService(os.getenv("CANAL_ARROBA"))

    def processar(self, produto: Produto) -> tuple[Path, Path]:
        """Executa o pipeline completo e devolve (imagem, mensagem) salvos."""
        log.info("  [1/6] Extraindo a foto oficial da Amazon...")
        link_real = self._amazon.resolver_link(produto.link)
        asin = self._amazon.extrair_asin(link_real)
        html = self._amazon.baixar_html(self._amazon.montar_url_limpa(asin))
        url_foto = self._amazon.extrair_imagem_principal(html)
        avaliacao = self._amazon.extrair_avaliacao(html)
        log.info("        ASIN %s, avaliação %s", asin, avaliacao or "não encontrada")

        log.info("  [2/6] Removendo o fundo (rembg, local)...")
        sem_fundo = self._imagem.remover_fundo(self._imagem.baixar_imagem(url_foto))
        self._imagem.salvar_png(sem_fundo, f"{asin}_sem_fundo")

        log.info("  [3/6] IA de visão escolhendo o cenário...")
        cenario = self._visao.gerar_cenario(sem_fundo)
        nome_produto = cenario.get("produto", "Produto")
        log.info("        %s (%s)", nome_produto, cenario.get("categoria", "?"))

        log.info("  [4/6] Ambientando com o PhotoRoom (sandbox)...")
        # Enquadrar antes garante margem para o cenário aparecer (CONTEXTO 6.1).
        enquadrada = self._imagem.enquadrar_produto(sem_fundo)
        ambientada = self._imagem.ambientar_com_ia(enquadrada, cenario["prompt"])

        log.info("  [5/6] Aplicando selo de avaliação e marca d'água...")
        final = self._composicao.compor(ambientada, avaliacao)
        caminho_imagem = self._imagem.salvar_png(final, f"{asin}_final")

        log.info("  [6/6] Escrevendo a mensagem...")
        texto = self._mensagem.gerar_mensagem(
            produto=nome_produto,
            categoria=cenario.get("categoria", ""),
            link=produto.link,  # o link ORIGINAL, que tem o código de afiliado
            preco_por=produto.preco_por,
            preco_de=produto.preco_de,
            cupom=produto.cupom,
        )
        caminho_texto = self._pasta_saida / f"{asin}_mensagem.txt"
        caminho_texto.write_text(texto, encoding="utf-8")

        return caminho_imagem, caminho_texto


# ---------------------------------------------------------------------- #
# Execução do lote
# ---------------------------------------------------------------------- #

def configurar_log() -> None:
    """Log simples no terminal, com horário, para acompanhar o lote."""
    # O terminal do Windows pode não ser UTF-8; sem isso os emojis/acentos quebram.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # O SDK da Anthropic loga cada requisição HTTP; isso polui o progresso.
    # (o nome do logger mudou entre versões do SDK; silenciamos os dois).
    for nome in ("httpx", "httpx2"):
        logging.getLogger(nome).setLevel(logging.WARNING)


def main() -> int:
    configurar_log()

    if not ARQUIVO_PRODUTOS.exists():
        log.error("Arquivo %s não encontrado. Crie-o com um link por linha.", ARQUIVO_PRODUTOS)
        return 1

    produtos = ler_produtos(ARQUIVO_PRODUTOS)
    if not produtos:
        log.error("Nenhum produto válido em %s.", ARQUIVO_PRODUTOS)
        return 1

    try:
        robo = Robo()
    except ERROS_DO_PIPELINE as erro:
        # Ex.: chave faltando no .env. Sem os serviços não há o que fazer.
        log.error("Não consegui iniciar o robô: %s", erro)
        return 1

    sucessos: list[str] = []
    falhas: list[str] = []

    for indice, produto in enumerate(produtos, 1):
        log.info("Produto %d de %d: %s", indice, len(produtos), produto.link)
        try:
            imagem, texto = robo.processar(produto)
        except ERROS_DO_PIPELINE as erro:
            # Um produto com problema não derruba o lote inteiro.
            log.error("  ✗ Falhou: %s", erro)
            falhas.append(f"{produto.link} -> {erro}")
        else:
            log.info("  ✓ Pronto: %s e %s", imagem, texto)
            sucessos.append(produto.link)

        if indice < len(produtos):
            time.sleep(SEGUNDOS_ENTRE_PRODUTOS)

    log.info("")
    log.info("Resumo: %d pronto(s), %d com falha.", len(sucessos), len(falhas))
    for falha in falhas:
        log.info("  ✗ %s", falha)
    if sucessos:
        log.info("Revise as peças em %s/ antes de publicar.", PASTA_SAIDA)

    return 0 if not falhas else 2


if __name__ == "__main__":
    sys.exit(main())
