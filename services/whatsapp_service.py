"""
Serviço de postagem no WhatsApp.

O Python não fala com o WhatsApp diretamente: ele chama a "ponte" em Node
(whatsapp/ponte.js), que mantém o WhatsApp Web conectado. Esta classe só
esconde o HTTP, para o robô pedir "poste isto" sem saber como é feito.

ATENÇÃO: canal do WhatsApp não tem API oficial. A ponte usa um caminho não
oficial e o número pode ser banido. Use somente com o número de teste.
"""

from __future__ import annotations

import os
from pathlib import Path

import requests


class WhatsAppError(Exception):
    """Falha ao falar com a ponte ou ao postar."""


class WhatsAppService:
    # Postar imagem pode demorar (upload pelo WhatsApp Web).
    TEMPO_LIMITE = 120

    def __init__(self, url_ponte: str | None = None) -> None:
        self._url = (url_ponte or os.getenv("URL_PONTE_WHATSAPP", "http://localhost:3000")).rstrip("/")

    def _chamar(self, metodo: str, rota: str, corpo: dict | None = None) -> dict | list:
        try:
            resposta = requests.request(
                metodo, f"{self._url}{rota}", json=corpo, timeout=self.TEMPO_LIMITE
            )
        except requests.RequestException as erro:
            raise WhatsAppError(f"Ponte do WhatsApp fora do ar: {erro}") from erro
        try:
            dados = resposta.json()
        except ValueError as erro:
            raise WhatsAppError(f"Resposta estranha da ponte: {resposta.text[:200]}") from erro
        if resposta.status_code != 200:
            raise WhatsAppError(dados.get("erro", f"HTTP {resposta.status_code}"))
        return dados

    def esta_pronto(self) -> bool:
        """True quando o número está pareado e conectado."""
        try:
            return bool(self._chamar("GET", "/status").get("pronto"))
        except WhatsAppError:
            return False

    def listar_canais(self) -> list[dict]:
        return self._chamar("GET", "/canais")

    def postar(self, canal: str, imagem: Path, texto: str) -> bool:
        """
        Posta a imagem com o texto como legenda (`canal` é o nome ou o id) e
        marca o post com a etiqueta "Conteúdo de IA" do WhatsApp.

        Devolve se a etiqueta entrou. Sem ela o post continua no ar, então
        isso é um aviso, não uma falha.
        """
        # A ponte lê a imagem do disco: os dois containers dividem a pasta de dados.
        resposta = self._chamar(
            "POST", "/postar", {"canal": canal, "imagem": str(imagem.resolve()), "texto": texto}
        )
        return bool(resposta.get("etiquetaIA"))

    def avisar(self, texto: str) -> None:
        """Manda uma mensagem para o próprio número (aviso para o operador)."""
        self._chamar("POST", "/avisar", {"texto": texto})


if __name__ == "__main__":
    servico = WhatsAppService()
    print("Pronto:", servico.esta_pronto())
    if servico.esta_pronto():
        print("Canais:", servico.listar_canais())
