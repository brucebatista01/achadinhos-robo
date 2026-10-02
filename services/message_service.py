"""
Serviço de mensagem (a "copy" da peça publicitária).

Monta o texto pronto para copiar/colar no canal, no padrão dos canais de
promoção brasileiros: headline emocional em caixa alta, nome do produto,
preço (de/por), cupom opcional e link de afiliado.

A responsabilidade foi dividida em duas partes de propósito:
- gerar_headline(): CRIATIVA, depende da IA (não determinística, custa dinheiro).
- formatar_mensagem(): MECÂNICA, pura e determinística (testável sem API).
Assim, um erro de formatação nunca gasta crédito, e a headline pode ser
trocada/revisada pelo operador sem refazer o resto.
"""

from __future__ import annotations

import os

from anthropic import Anthropic, APIError
from dotenv import load_dotenv

load_dotenv()


class MessageError(Exception):
    """Exceção específica do serviço de mensagem, para tratamento isolado no main."""


class MessageService:
    # Haiku: a headline é um texto curto; não precisa de modelo mais caro.
    _MODELO = "claude-haiku-4-5"

    # Limite de segurança: uma headline boa cabe numa linha do celular.
    _MAX_CARACTERES_HEADLINE = 60

    # Instrução que ensina o Claude a agir como copywriter de canal de ofertas.
    _INSTRUCAO = (
        "Você é copywriter de um canal brasileiro de ofertas no WhatsApp. "
        "Crie UMA headline para o produto abaixo.\n\n"
        "Produto: {produto}\n"
        "Categoria: {categoria}\n\n"
        "REGRAS:\n"
        "1. Venda o BENEFÍCIO ou a SENSAÇÃO, nunca descreva o produto.\n"
        "2. Português do Brasil, tom informal e direto.\n"
        "3. Entre 4 e 9 palavras.\n"
        "4. NÃO cite marca, nome do produto, preço, desconto ou emojis.\n"
        "5. Use só palavras que existem no dicionário; revise ortografia e "
        "acentuação antes de responder.\n"
        "6. Responda APENAS com a headline, sem aspas e sem explicações.\n\n"
        "EXEMPLOS de boas headlines:\n"
        "- VIRA DIA ONDE VOCÊ APONTAR ESSA LUZ (lanterna)\n"
        "- DESLIGAR A MENTE NUNCA FOI TÃO GOSTOSO (suplemento de magnésio)\n"
        "- LIBERDADE PRA PASSAR O DIA LONGE DA TOMADA (power bank)"
    )

    def __init__(self) -> None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise MessageError(
                "Chave ANTHROPIC_API_KEY não encontrada (verifique o .env)."
            )
        self._client = Anthropic(api_key=api_key)

    # ------------------------------------------------------------------ #
    # Parte criativa (IA)
    # ------------------------------------------------------------------ #

    def gerar_headline(self, produto: str, categoria: str) -> str:
        """
        Pede à IA uma headline emocional para o produto.

        Recebe nome e categoria (já identificados pelo VisionService), e não a
        imagem: texto é muito mais barato que visão, e a visão já fez o
        trabalho de entender o que é o produto.
        """
        instrucao = self._INSTRUCAO.format(produto=produto, categoria=categoria)

        try:
            resposta = self._client.messages.create(
                model=self._MODELO,
                max_tokens=60,
                messages=[{"role": "user", "content": instrucao}],
            )
        except APIError as erro:
            raise MessageError(f"Falha na API da Anthropic: {erro}") from erro

        return self._limpar_headline(resposta.content[0].text)

    def _limpar_headline(self, texto: str) -> str:
        """
        Normaliza a resposta da IA. Mesmo com instrução clara, o modelo às
        vezes devolve aspas, ponto final ou mais de uma linha — aqui garantimos
        o formato em vez de confiar cegamente na IA.
        """
        linhas = [linha.strip() for linha in texto.strip().splitlines() if linha.strip()]
        if not linhas:
            raise MessageError("A IA retornou uma headline vazia.")

        headline = linhas[0].strip("\"'“”- ").rstrip(".").upper()

        if not headline:
            raise MessageError(f"Headline inválida. Resposta da IA: {texto!r}")
        if len(headline) > self._MAX_CARACTERES_HEADLINE:
            raise MessageError(
                f"Headline longa demais ({len(headline)} caracteres): {headline!r}"
            )
        return headline

    # ------------------------------------------------------------------ #
    # Parte mecânica (formatação pura, sem IA)
    # ------------------------------------------------------------------ #

    @staticmethod
    def formatar_preco(valor: float) -> str:
        """
        Formata um número no padrão brasileiro: 1234.5 -> 'R$ 1.234,50'.

        Feito à mão em vez de usar `locale`, porque o locale depende da
        configuração do Windows de cada máquina — o Danilo poderia ver
        resultado diferente do meu.
        """
        if valor < 0:
            raise MessageError(f"Preço negativo não faz sentido: {valor}")
        texto = f"{valor:,.2f}"  # '1,234.50' (padrão americano)
        texto = texto.replace(",", "_").replace(".", ",").replace("_", ".")
        return f"R$ {texto}"

    @classmethod
    def formatar_mensagem(
        cls,
        headline: str,
        produto: str,
        link: str,
        preco_por: float,
        preco_de: float | None = None,
        cupom: str | None = None,
    ) -> str:
        """
        Monta a mensagem final, pronta para copiar e colar.

        `preco_de` e `cupom` são opcionais: nem toda oferta tem preço
        riscado ou cupom, e a linha simplesmente some quando não há.
        """
        if not link.startswith(("http://", "https://")):
            raise MessageError(f"Link de afiliado inválido: {link!r}")
        if preco_de is not None and preco_de <= preco_por:
            # "DE R$ 100 | POR R$ 120" seria propaganda enganosa; melhor omitir.
            preco_de = None

        if preco_de is not None:
            linha_preco = (
                f"🔥 DE {cls.formatar_preco(preco_de)} | "
                f"POR {cls.formatar_preco(preco_por)}"
            )
        else:
            linha_preco = f"🔥 POR {cls.formatar_preco(preco_por)}"

        blocos = [headline.upper(), f"✅ {produto}", linha_preco]
        if cupom:
            blocos.append(f"🎟️ CUPOM: {cupom.strip().upper()}")
        blocos.append(f"🔗 {link}")

        # Linha em branco entre blocos: é o respiro visual dos canais de referência.
        return "\n\n".join(blocos)

    # ------------------------------------------------------------------ #
    # Atalho para o orquestrador
    # ------------------------------------------------------------------ #

    def gerar_mensagem(
        self,
        produto: str,
        categoria: str,
        link: str,
        preco_por: float,
        preco_de: float | None = None,
        cupom: str | None = None,
    ) -> str:
        """Gera a headline com a IA e já devolve a mensagem completa."""
        headline = self.gerar_headline(produto, categoria)
        return self.formatar_mensagem(headline, produto, link, preco_por, preco_de, cupom)


# Bloco de teste rápido.
if __name__ == "__main__":
    try:
        servico = MessageService()
        print("Gerando headline com a IA...\n")
        mensagem = servico.gerar_mensagem(
            produto="Lanterna Tática LED Recarregável",
            categoria="Ferramentas e iluminação",
            link="https://amzn.to/exemplo",
            preco_por=169.97,
            preco_de=278.65,
            cupom="LUZ10",
        )
        print("--- Mensagem pronta ---\n")
        print(mensagem)
    except MessageError as erro:
        print(f"[ERRO] {erro}")
