"""Testes do cadastro de clientes (vários donos de canal no mesmo robô)."""

from __future__ import annotations

import json

import pytest

import clientes


@pytest.fixture(autouse=True)
def dados(tmp_path, monkeypatch):
    monkeypatch.setattr(clientes, "PASTA_DADOS", tmp_path)
    monkeypatch.setattr(clientes, "ARQUIVO_CLIENTES", tmp_path / "clientes.json")
    monkeypatch.setattr(clientes, "ARQUIVO_COMPOSE", tmp_path / "compose.yml")
    monkeypatch.setenv("WHATSAPP_CANAL", "Promo00")
    monkeypatch.setenv("PAINEL_TOKEN", "t" * 48)
    return tmp_path


def test_principal_vem_do_env_e_usa_a_raiz(dados):
    (principal,) = clientes.carregar()
    assert principal.id == "principal"
    assert principal.canal == "Promo00"
    assert principal.pasta == dados


def test_novo_cliente_ganha_pasta_link_e_ponte_proprios(dados):
    novo = clientes.novo("joao", canal="Ofertas do João", etiqueta="joao-20", pin="4321")
    assert (dados / "clientes" / "joao" / "output").is_dir()
    assert json.loads((dados / "clientes" / "joao" / "estado.json").read_text())["ligado"] is False
    assert len(novo.painel_token) == 48
    assert novo.url_whatsapp == "http://whatsapp-joao:3000"
    assert "whatsapp-joao:" in (dados / "compose.yml").read_text()
    assert [c.id for c in clientes.carregar()] == ["principal", "joao"]
    assert clientes.por_token(novo.painel_token).id == "joao"
    assert clientes.por_token("errado" * 8) is None


def test_ids_invalidos_ou_repetidos_sao_recusados(dados):
    clientes.novo("joao", "C", "t-20", "1234")
    for id_ in ("joao", "Principal!", "principal", "a"):
        with pytest.raises(ValueError):
            clientes.novo(id_, "C", "t-20", "1234")
    with pytest.raises(ValueError):
        clientes.novo("maria", "C", "t-20", "12")  # PIN curto


def test_remover_mantem_a_pasta(dados):
    clientes.novo("joao", "C", "t-20", "1234")
    clientes.remover("joao")
    assert [c.id for c in clientes.carregar()] == ["principal"]
    assert (dados / "clientes" / "joao").is_dir()
    assert (dados / "compose.yml").read_text() == "services: {}\n"
