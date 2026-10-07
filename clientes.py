"""
Clientes do robô: vários donos de canal no mesmo servidor.

Cada cliente tem o seu canal, a sua etiqueta de afiliado, o seu link de
painel com PIN, o seu WhatsApp (uma ponte própria, com a sessão do número
dele) e a sua pasta de dados (lista, estado, configurações, peças, histórico).
O que é dividido entre todos: o processo do robô, o painel e o modelo de
recorte de imagem (~1 GB de memória), que é o que mais pesa. Assim cada
cliente novo custa só a ponte do WhatsApp dele (~550 MB).

O cliente "principal" é o configurado no .env e usa a raiz de `dados/`
(o jeito de antes, para nada mudar para quem já usava). Os demais ficam em
`dados/clientes.json` e em `dados/clientes/<id>/`.

Comandos:
    python clientes.py listar
    python clientes.py novo <id> --canal "Nome do canal" --tag etiqueta-20 --pin 1234 [--nome "Fulano"] [--arroba @canal]
    python clientes.py remover <id>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PASTA_DADOS = Path(os.getenv("PASTA_DADOS", "dados"))
ARQUIVO_CLIENTES = PASTA_DADOS / "clientes.json"
# Serviços de WhatsApp dos clientes extras, para o Docker Compose subir.
ARQUIVO_COMPOSE = Path(os.getenv("ARQUIVO_COMPOSE_CLIENTES", "docker-compose.clientes.yml"))


@dataclass(frozen=True)
class Cliente:
    id: str
    nome: str
    pasta: Path
    canal: str
    etiqueta: str
    painel_token: str
    painel_pin: str
    url_whatsapp: str
    arroba: str = ""

    # Arquivos do cliente: tudo dentro da pasta dele.
    @property
    def lista(self) -> Path:
        return self.pasta / "lista.txt"

    @property
    def estado(self) -> Path:
        return self.pasta / "estado.json"

    @property
    def config(self) -> Path:
        return self.pasta / "config.json"

    @property
    def cache(self) -> Path:
        return self.pasta / "pecas.json"

    @property
    def historico(self) -> Path:
        return self.pasta / "postados.log"

    @property
    def posts(self) -> Path:
        return self.pasta / "postados.jsonl"

    @property
    def postar_agora(self) -> Path:
        return self.pasta / "postar_agora"

    @property
    def pecas(self) -> Path:
        return self.pasta / "output"

    @property
    def qr(self) -> Path:
        return self.pasta / "qr.png"


def principal() -> Cliente:
    """O cliente do .env, na raiz de dados/ (o mesmo de antes dos clientes)."""
    return Cliente(
        id="principal",
        nome=os.getenv("NOME_CLIENTE", "Principal"),
        pasta=PASTA_DADOS,
        canal=os.getenv("WHATSAPP_CANAL", ""),
        etiqueta=os.getenv("AMAZON_TAG", ""),
        painel_token=os.getenv("PAINEL_TOKEN", ""),
        painel_pin=os.getenv("PAINEL_PIN", ""),
        url_whatsapp=os.getenv("URL_PONTE_WHATSAPP", "http://localhost:3000"),
        arroba=os.getenv("CANAL_ARROBA", ""),
    )


def _ler_extras() -> list[dict]:
    if not ARQUIVO_CLIENTES.exists():
        return []
    return json.loads(ARQUIVO_CLIENTES.read_text(encoding="utf-8"))


def carregar() -> list[Cliente]:
    """Todos os clientes: o principal (se tiver canal) e os do clientes.json."""
    clientes = [principal()] if os.getenv("WHATSAPP_CANAL") else []
    for dados in _ler_extras():
        clientes.append(Cliente(**{**dados, "pasta": PASTA_DADOS / "clientes" / dados["id"]}))
    return clientes


def por_token(token: str) -> Cliente | None:
    """O cliente dono deste link de painel (comparação em tempo constante)."""
    for cliente in carregar():
        if len(cliente.painel_token) >= 16 and secrets.compare_digest(token, cliente.painel_token):
            return cliente
    return None


# ---------------------------------------------------------------------- #
# Cadastro (linha de comando)
# ---------------------------------------------------------------------- #

def _salvar_extras(extras: list[dict]) -> None:
    ARQUIVO_CLIENTES.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO_CLIENTES.write_text(json.dumps(extras, ensure_ascii=False, indent=2), encoding="utf-8")
    _escrever_compose(extras)


def _escrever_compose(extras: list[dict]) -> None:
    """
    Uma ponte de WhatsApp por cliente extra, cada uma com a pasta do cliente
    (é lá que fica a sessão do número dele e o QR de pareamento).
    """
    linhas = [
        "# Gerado por clientes.py: uma ponte de WhatsApp por cliente extra.",
        "# Não edite à mão; use  python clientes.py novo/remover.",
        "services:",
    ]
    for dados in extras:
        linhas += [
            f"  whatsapp-{dados['id']}:",
            "    build:",
            "      context: .",
            "      dockerfile: Dockerfile.whatsapp",
            "    restart: unless-stopped",
            "    volumes:",
            f"      - ./dados/clientes/{dados['id']}:/dados",
        ]
    texto = "\n".join(linhas) + "\n" if extras else "services: {}\n"
    ARQUIVO_COMPOSE.write_text(texto, encoding="utf-8")


def novo(id_: str, canal: str, etiqueta: str, pin: str, nome: str = "", arroba: str = "") -> Cliente:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,30}", id_) or id_ == "principal":
        raise ValueError("Use um id curto, só com letras minúsculas, números e '-' (ex.: joao).")
    extras = _ler_extras()
    if any(dados["id"] == id_ for dados in extras):
        raise ValueError(f"Já existe um cliente com o id {id_!r}.")
    if len(pin) < 4:
        raise ValueError("O PIN precisa de pelo menos 4 caracteres.")
    dados = {
        "id": id_,
        "nome": nome or id_,
        "canal": canal,
        "etiqueta": etiqueta,
        "painel_token": secrets.token_hex(24),
        "painel_pin": pin,
        "url_whatsapp": f"http://whatsapp-{id_}:3000",
        "arroba": arroba,
    }
    pasta = PASTA_DADOS / "clientes" / id_
    (pasta / "output").mkdir(parents=True, exist_ok=True)
    # Começa desligado: só posta depois de conectar o WhatsApp e ter lista.
    (pasta / "estado.json").write_text(
        json.dumps({"ligado": False, "motivo": "recém-criado", "ultimo": "", "ultimo_em": ""}), encoding="utf-8"
    )
    (pasta / "lista.txt").write_text(
        "# Um produto por linha:  link | preço | preço antigo | cupom\n", encoding="utf-8"
    )
    extras.append(dados)
    _salvar_extras(extras)
    return Cliente(**{**dados, "pasta": pasta})


def remover(id_: str) -> None:
    """Tira o cliente do robô. A pasta dele fica guardada (não apaga dados)."""
    extras = _ler_extras()
    restantes = [dados for dados in extras if dados["id"] != id_]
    if len(restantes) == len(extras):
        raise ValueError(f"Cliente {id_!r} não encontrado.")
    _salvar_extras(restantes)


def main(argumentos: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Clientes do robô de achadinhos")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("listar")
    p_novo = sub.add_parser("novo")
    p_novo.add_argument("id")
    p_novo.add_argument("--canal", required=True)
    p_novo.add_argument("--tag", required=True)
    p_novo.add_argument("--pin", required=True)
    p_novo.add_argument("--nome", default="")
    p_novo.add_argument("--arroba", default="")
    p_remover = sub.add_parser("remover")
    p_remover.add_argument("id")
    args = parser.parse_args(argumentos)

    dominio = os.getenv("DOMINIO_PAINEL", "")
    try:
        if args.comando == "listar":
            for cliente in carregar():
                print(f"{cliente.id:12} canal={cliente.canal!r:24} tag={cliente.etiqueta:16} pasta={cliente.pasta}")
        elif args.comando == "novo":
            cliente = novo(args.id, args.canal, args.tag, args.pin, args.nome, args.arroba)
            print(f"Cliente {cliente.id} criado.")
            print(f"Painel: https://{dominio or '<endereço>'}/p/{cliente.painel_token}/  (PIN: {cliente.painel_pin})")
            print("Próximo passo: subir a ponte do WhatsApp dele e reiniciar robô e painel (robo clientes-aplicar).")
        else:
            remover(args.id)
            print(f"Cliente {args.id} removido (a pasta dele foi mantida).")
    except ValueError as erro:
        print(f"Erro: {erro}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
