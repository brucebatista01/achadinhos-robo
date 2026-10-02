"""
Modo lote pelo terminal: processa todos os produtos de `produtos.txt`.

A lógica de cada produto mora em `pipeline.py` (compartilhada com o app web).
Aqui fica só o que é específico do lote: ler o arquivo, mostrar o progresso
no terminal, seguir em frente quando um produto falha e resumir no final.

Importante: o robô entrega a peça pronta e NÃO publica no WhatsApp.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from pipeline import (
    ERROS_DO_PIPELINE,
    ETAPAS,
    PASTA_SAIDA,
    Oferta,
    Pipeline,
    Produto,
    ProdutoInvalidoError,
    converter_preco,
)

ARQUIVO_PRODUTOS = Path("produtos.txt")

# Pausa entre produtos: evita martelar a Amazon (que bloqueia robôs
# apressados) e as APIs pagas.
SEGUNDOS_ENTRE_PRODUTOS = 5

log = logging.getLogger("robo")


# ---------------------------------------------------------------------- #
# Leitura do produtos.txt
# ---------------------------------------------------------------------- #

def interpretar_linha(linha: str) -> Produto:
    """
    Formato: link | preco_por | preco_de | cupom
    Só link e preco_por são obrigatórios.

    O lote pelo terminal gera só o post da Amazon; os posts das outras
    lojas são feitos pelo app web, que tem um campo para cada loja.
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

    oferta = Oferta(
        loja="amazon",
        link=link,
        preco_por=converter_preco(preco_por),
        preco_de=converter_preco(preco_de) if preco_de else None,
        cupom=cupom or None,
    )
    return Produto(link_amazon=link, ofertas=(oferta,))


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


def mostrar_etapa(numero: int, descricao: str) -> None:
    """Callback de progresso do pipeline, versão terminal."""
    log.info("  [%d/%d] %s...", numero, len(ETAPAS), descricao)


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
        pipeline = Pipeline()
    except ERROS_DO_PIPELINE as erro:
        # Ex.: chave faltando no .env. Sem os serviços não há o que fazer.
        log.error("Não consegui iniciar o robô: %s", erro)
        return 1

    sucessos: list[str] = []
    falhas: list[str] = []

    for indice, produto in enumerate(produtos, 1):
        log.info("Produto %d de %d: %s", indice, len(produtos), produto.link_amazon)
        try:
            peca = pipeline.processar(produto, ao_avancar=mostrar_etapa)
        except ERROS_DO_PIPELINE as erro:
            # Um produto com problema não derruba o lote inteiro.
            log.error("  ✗ Falhou: %s", erro)
            falhas.append(f"{produto.link_amazon} -> {erro}")
        else:
            log.info("  ✓ Pronto: %s (+ texto em %s/%s_amazon.txt)",
                     peca.caminho_imagem, PASTA_SAIDA, peca.asin)
            sucessos.append(produto.link_amazon)

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
