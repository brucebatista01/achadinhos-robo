"""
Liga o app web do robô.

Uso:  python servidor.py
Depois abra o link que aparece no terminal.

HOST e PORT podem vir do ambiente: em hospedagens (ex.: Hugging Face) o
servidor precisa escutar em 0.0.0.0 numa porta definida por elas.
"""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from main import configurar_log
from web.app import criar_app, obter_token


def main() -> None:
    configurar_log()
    host = os.getenv("HOST", "127.0.0.1")
    porta = int(os.getenv("PORT", "8000"))
    token = obter_token()

    log = logging.getLogger("web")
    if host == "127.0.0.1":
        log.info("Abra no navegador: http://localhost:%d/p/%s/", porta, token)
    else:
        # Na hospedagem, os logs podem ser vistos por outras pessoas da
        # plataforma: nunca escrevemos o código secreto neles.
        log.info("App no ar na porta %d. O link é /p/<APP_TOKEN>/.", porta)

    # log_level="warning": o uvicorn não precisa logar cada consulta de
    # progresso (a página consulta a cada segundo).
    uvicorn.run(criar_app(token), host=host, port=porta, log_level="warning")


if __name__ == "__main__":
    sys.exit(main())
