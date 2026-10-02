"""
Peças compartilhadas pelos testes.

Regra destes testes: NENHUM acessa a internet nem gasta crédito de API.
As chamadas externas são substituídas por dublês (fakes), o que deixa a
suíte rápida, gratuita e com o mesmo resultado sempre.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class ClienteAnthropicFalso:
    """Imita o `client.messages.create()` do SDK, devolvendo um texto fixo."""

    def __init__(self, resposta: str) -> None:
        self.resposta = resposta
        self.chamadas: list[dict] = []
        self.messages = self  # permite client.messages.create(...)

    def create(self, **kwargs):
        self.chamadas.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text=self.resposta)])


@pytest.fixture
def chave_falsa(monkeypatch):
    """Coloca uma chave de mentira no ambiente; os serviços só checam se existe."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teste")
