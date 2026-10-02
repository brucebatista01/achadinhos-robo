"""
Núcleo do robô: o pipeline que transforma UM produto em UMA peça pronta.

extração -> remoção de fundo -> visão -> ambientação -> composição -> mensagem

Este módulo não sabe quem o chama. Pode ser o `main.py` (lote pelo terminal)
ou o app web (um produto por vez, pela interface). Por isso o progresso é
avisado por uma função de callback (`ao_avancar`), e não com print/log:
o terminal escreve o progresso no log, o app web mostra na tela.

O pipeline NÃO sabe COMO cada etapa funciona, só a ORDEM delas. Cada serviço
resolve o seu problema e avisa falhas pela própria exceção.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from services.amazon_service import AmazonError, AmazonService
from services.composition_service import CompositionError, CompositionService
from services.image_service import ImageError, ImageService
from services.message_service import MessageError, MessageService
from services.vision_service import VisionError, VisionService

load_dotenv()

PASTA_SAIDA = Path("output")

# Todas as falhas "esperadas" do pipeline. Qualquer outra exceção é bug
# de verdade e merece aparecer com o traceback completo.
ERROS_DO_PIPELINE = (
    AmazonError, ImageError, VisionError, CompositionError, MessageError
)

# Nomes das etapas, na ordem. Ficam aqui (e não espalhados no código) para
# que o terminal e a interface web mostrem exatamente os mesmos passos.
ETAPAS = (
    "Buscando a foto oficial na Amazon",
    "Recortando o fundo",
    "Escolhendo o cenário com IA",
    "Montando a cena",
    "Aplicando o selo de avaliação",
    "Escrevendo a mensagem",
)

# Assinatura do callback de progresso: (número da etapa a partir de 1, descrição).
AoAvancar = Callable[[int, str], None]


class ProdutoInvalidoError(ValueError):
    """Dados do produto mal preenchidos (preço inválido, link vazio...)."""


@dataclass(frozen=True)
class Produto:
    """O que o operador informa: o link e os dados da oferta."""

    link: str
    preco_por: float
    preco_de: float | None = None
    cupom: str | None = None


@dataclass(frozen=True)
class Peca:
    """O resultado: a peça pronta para revisar e publicar."""

    asin: str
    nome_produto: str
    caminho_imagem: Path
    caminho_mensagem: Path
    mensagem: str


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


def _nao_avisar(numero: int, descricao: str) -> None:
    """Callback padrão: quem não quer acompanhar o progresso não recebe nada."""


class Pipeline:
    """
    Guarda os serviços e processa um produto por vez.

    Os serviços são criados UMA vez e reaproveitados: abrir conexões,
    validar chaves e carregar o modelo de recorte (~1 GB) a cada produto
    seria desperdício.
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

    def processar(self, produto: Produto, ao_avancar: AoAvancar = _nao_avisar) -> Peca:
        """Executa o pipeline completo e devolve a peça salva em disco."""
        ao_avancar(1, ETAPAS[0])
        link_real = self._amazon.resolver_link(produto.link)
        asin = self._amazon.extrair_asin(link_real)
        html = self._amazon.baixar_html(self._amazon.montar_url_limpa(asin))
        url_foto = self._amazon.extrair_imagem_principal(html)
        avaliacao = self._amazon.extrair_avaliacao(html)

        ao_avancar(2, ETAPAS[1])
        sem_fundo = self._imagem.remover_fundo(self._imagem.baixar_imagem(url_foto))
        self._imagem.salvar_png(sem_fundo, f"{asin}_sem_fundo")

        ao_avancar(3, ETAPAS[2])
        cenario = self._visao.gerar_cenario(sem_fundo)
        nome_produto = cenario.get("produto", "Produto")

        ao_avancar(4, ETAPAS[3])
        # Enquadrar antes garante margem para o cenário aparecer.
        enquadrada = self._imagem.enquadrar_produto(sem_fundo)
        ambientada = self._imagem.ambientar_com_ia(enquadrada, cenario["prompt"])

        ao_avancar(5, ETAPAS[4])
        final = self._composicao.compor(ambientada, avaliacao)
        caminho_imagem = self._imagem.salvar_png(final, f"{asin}_final")

        ao_avancar(6, ETAPAS[5])
        mensagem = self._mensagem.gerar_mensagem(
            produto=nome_produto,
            categoria=cenario.get("categoria", ""),
            link=produto.link,  # o link ORIGINAL, que tem o código de afiliado
            preco_por=produto.preco_por,
            preco_de=produto.preco_de,
            cupom=produto.cupom,
        )
        caminho_mensagem = self._pasta_saida / f"{asin}_mensagem.txt"
        caminho_mensagem.write_text(mensagem, encoding="utf-8")

        return Peca(asin, nome_produto, caminho_imagem, caminho_mensagem, mensagem)
