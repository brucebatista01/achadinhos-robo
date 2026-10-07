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


class PipelineError(Exception):
    """Falha do próprio pipeline (ex.: oferta sem link de afiliado)."""


# Todas as falhas "esperadas" do pipeline. Qualquer outra exceção é bug
# de verdade e merece aparecer com o traceback completo.
ERROS_DO_PIPELINE = (
    AmazonError, ImageError, VisionError, CompositionError, MessageError, PipelineError
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


# Lojas em que o Danilo é afiliado, na ordem em que aparecem na tela.
# A chave é o identificador interno; o valor, o nome mostrado ao usuário.
LOJAS = {
    "amazon": "Amazon",
    "shopee": "Shopee",
    "mercadolivre": "Mercado Livre",
    "magalu": "Magalu",
}


@dataclass(frozen=True)
class Oferta:
    """O produto à venda numa loja: link de afiliado e preços DAQUELA loja."""

    loja: str  # uma das chaves de LOJAS
    preco_por: float
    # Na Amazon pode ficar vazio: o robô monta o link com a etiqueta de
    # afiliado (AMAZON_TAG). Nas outras lojas é obrigatório.
    link: str | None = None
    preco_de: float | None = None
    cupom: str | None = None


@dataclass(frozen=True)
class Produto:
    """
    O que o operador informa.

    A foto e a avaliação vêm SEMPRE da Amazon (`link_amazon`), que é a única
    loja que deixa o robô ler a página. As ofertas dizem onde o produto está
    à venda; cada uma vira um post com o seu link e o seu preço.
    """

    link_amazon: str
    ofertas: tuple[Oferta, ...]


@dataclass(frozen=True)
class Peca:
    """O resultado: uma imagem e um texto pronto para cada loja."""

    asin: str
    nome_produto: str
    caminho_imagem: Path
    mensagens: dict[str, str]  # loja -> texto pronto para colar
    # (nota, quantidade) usados no selo; None = produto sem avaliações na
    # Amazon (anúncio novo), e a imagem sai sem selo.
    avaliacao: tuple[float, int] | None = None


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

    def __init__(
        self,
        pasta_saida: Path = PASTA_SAIDA,
        arroba_canal: str | None = None,
        etiqueta_afiliado: str | None = None,
    ) -> None:
        self._pasta_saida = pasta_saida
        self._amazon = AmazonService()
        self._imagem = ImageService(pasta_saida=str(pasta_saida))
        self._visao = VisionService()
        self._mensagem = MessageService()
        # O @ do canal não é segredo, mas fica no .env para o Danilo trocar
        # sem mexer no código. Sem ele, a peça sai sem marca d'água.
        self._composicao = CompositionService(arroba_canal or os.getenv("CANAL_ARROBA"))
        # Cada cliente do robô tem a sua etiqueta; sem uma, vale a do .env.
        self._etiqueta = etiqueta_afiliado

    def processar(self, produto: Produto, ao_avancar: AoAvancar = _nao_avisar) -> Peca:
        """Executa o pipeline completo e devolve a peça salva em disco."""
        ao_avancar(1, ETAPAS[0])
        link_real = self._amazon.resolver_link(produto.link_amazon)
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
        # A IA redesenha o produto junto com o cenário e troca letras e cores
        # (capas de livro mudavam). O produto original volta por cima.
        ambientada = self._imagem.preservar_produto(ambientada, enquadrada)

        ao_avancar(5, ETAPAS[4])
        final = self._composicao.compor(ambientada, avaliacao)
        caminho_imagem = self._imagem.salvar_png(final, f"{asin}_final")

        ao_avancar(6, ETAPAS[5])
        # Uma headline só (uma chamada à IA), reaproveitada em todos os posts:
        # o produto é o mesmo, só mudam o link e o preço.
        headline = self._mensagem.gerar_headline(nome_produto, cenario.get("categoria", ""))
        mensagens: dict[str, str] = {}
        for oferta in produto.ofertas:
            texto = self._mensagem.formatar_mensagem(
                headline=headline,
                produto=nome_produto,
                link=self._link_da_oferta(oferta, produto.link_amazon, asin),
                preco_por=oferta.preco_por,
                preco_de=oferta.preco_de,
                cupom=oferta.cupom,
            )
            (self._pasta_saida / f"{asin}_{oferta.loja}.txt").write_text(texto, encoding="utf-8")
            mensagens[oferta.loja] = texto

        return Peca(asin, nome_produto, caminho_imagem, mensagens, avaliacao)

    def _link_da_oferta(self, oferta: Oferta, link_amazon: str, asin: str) -> str:
        """
        O link que vai no post. Se o operador colou um link, ele manda.
        Na Amazon sem link colado, montamos o de afiliado pela etiqueta.
        """
        if oferta.link:
            return oferta.link
        if oferta.loja != "amazon":
            raise PipelineError(f"Falta o link de afiliado da {LOJAS[oferta.loja]}.")
        etiqueta = getattr(self, "_etiqueta", None) or os.getenv("AMAZON_TAG")
        return self._amazon.montar_link_afiliado(asin, etiqueta) or link_amazon
