"""
Serviço responsável por extrair informações de produtos da Amazon
a partir de links (normais ou de afiliado).

Fluxo: extrair o ASIN do link -> montar URL limpa -> baixar HTML ->
extrair a URL da imagem principal em alta resolução a partir do
bloco JSON 'colorImages'.
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote

import requests


class AmazonError(Exception):
    """Exceção específica do serviço da Amazon, para tratamento isolado no main."""


class AmazonService:
    _HEADERS_PADRAO = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    def __init__(self, timeout: int = 15) -> None:
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(self._HEADERS_PADRAO)

    def extrair_asin(self, link: str) -> str:
        """
        Extrai o ASIN (código único do produto) de um link da Amazon.

        O ASIN aparece após '/dp/' ou '/gp/product/' na URL.
        """
        padrao = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")
        casamento = padrao.search(link)
        if not casamento:
            raise AmazonError(f"Não foi possível encontrar o ASIN no link: {link!r}")
        return casamento.group(1)

    def resolver_link(self, link: str) -> str:
        """
        Expande links curtos de afiliado (ex.: https://amzn.to/abc123).

        O link curto não contém o ASIN: ele só redireciona para a página
        real do produto. Seguimos o redirecionamento e devolvemos a URL
        final. Links que já têm ASIN são devolvidos sem nenhuma requisição.
        """
        try:
            self.extrair_asin(link)
            return link
        except AmazonError:
            pass

        try:
            resposta = self._session.get(
                link, timeout=self._timeout, allow_redirects=True
            )
        except requests.RequestException as erro:
            raise AmazonError(f"Falha ao expandir o link curto: {erro}") from erro
        return resposta.url

    def montar_url_limpa(self, asin: str) -> str:
        """Monta uma URL de produto limpa, sem parâmetros de rastreamento."""
        return f"https://www.amazon.com.br/dp/{asin}"

    def montar_link_afiliado(self, asin: str, etiqueta: str | None) -> str | None:
        """
        Monta o link de afiliado a partir da etiqueta (ex.: 'danilo-20').

        O link de afiliado da Amazon é só o link do produto com a etiqueta
        no parâmetro 'tag'; é isso que o SiteStripe gera. Sem etiqueta
        configurada, devolve None e quem chamou decide o que usar.
        """
        etiqueta = (etiqueta or "").strip()
        if not etiqueta:
            return None
        return f"{self.montar_url_limpa(asin)}?tag={quote(etiqueta)}"

    def baixar_html(self, url_produto: str) -> str:
        """Baixa o HTML bruto da página do produto."""
        try:
            resposta = self._session.get(url_produto, timeout=self._timeout)
            resposta.raise_for_status()
        except requests.RequestException as erro:
            raise AmazonError(f"Falha ao baixar HTML: {erro}") from erro
        return resposta.text

    def extrair_imagem_principal(self, html: str) -> str:
        """
        Extrai a URL da imagem principal do produto em alta resolução.

        Estratégia primária: localizar o bloco JSON 'colorImages', parsear
        e pegar o campo 'hiRes' do objeto cuja 'variant' é 'MAIN'.
        Estratégia de fallback: regex direto atrás de uma URL '_SL1500_'.
        """
        # --- Estratégia primária: parsear o JSON de colorImages ---
        # A Amazon escreve: 'initial': A.$.parseJSON('[ ...json... ]')
        padrao_bloco = re.compile(r"'colorImages'.*?parseJSON\('(\[.*?\])'\)", re.DOTALL)
        casamento = padrao_bloco.search(html)

        if casamento:
            bloco_json = casamento.group(1)
            try:
                imagens = json.loads(bloco_json)
                # Procura o objeto marcado como imagem principal (MAIN).
                for imagem in imagens:
                    if imagem.get("variant") == "MAIN" and imagem.get("hiRes"):
                        return imagem["hiRes"]
                # Se não achou MAIN, usa o hiRes do primeiro item que tiver.
                for imagem in imagens:
                    if imagem.get("hiRes"):
                        return imagem["hiRes"]
            except json.JSONDecodeError:
                pass  # cai para o fallback abaixo

        # --- Fallback: regex atrás de uma URL de alta resolução (_SL1500_) ---
        padrao_hires = re.compile(
            r"https://[a-zA-Z0-9.\-]*media-amazon\.com/images/I/"
            r"[A-Za-z0-9._\-]+_SL1500_[A-Za-z0-9._\-]*\.jpg"
        )
        casamento_fb = padrao_hires.search(html)
        if casamento_fb:
            return casamento_fb.group(0)

        raise AmazonError("Não foi possível extrair a imagem principal do produto.")

    def extrair_avaliacao(self, html: str) -> tuple[float, int] | None:
        """
        Extrai (nota, quantidade de avaliações), ex.: (4.8, 3423).

        Retorna None em vez de lançar exceção: produto novo, sem avaliações,
        é um caso normal. A peça só fica sem o selo, mas continua válida.
        """
        nota = re.search(r'id="acrPopover"[^>]*title="(\d+,\d) de 5', html)
        quantidade = re.search(r'id="acrCustomerReviewText"[^>]*>\(?([\d.]+)', html)
        if not nota or not quantidade:
            return None
        return (
            float(nota.group(1).replace(",", ".")),
            int(quantidade.group(1).replace(".", "")),
        )


# Bloco de teste rápido.
if __name__ == "__main__":
    servico = AmazonService()
    link = input("Cole um link de produto da Amazon: ").strip()
    try:
        asin = servico.extrair_asin(servico.resolver_link(link))
        url_limpa = servico.montar_url_limpa(asin)
        print(f"ASIN extraído: {asin}")
        print(f"URL limpa: {url_limpa}")

        html = servico.baixar_html(url_limpa)
        imagem = servico.extrair_imagem_principal(html)

        print(f"\n✓ Imagem principal extraída:")
        print(imagem)
        print(f"Avaliação (nota, quantidade): {servico.extrair_avaliacao(html)}")

    except AmazonError as erro:
        print(f"[ERRO] {erro}")