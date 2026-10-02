"""
App web do robô: a mesma esteira do `main.py`, mas com interface.

O operador cola o link e os preços, acompanha as etapas na tela e recebe a
peça pronta com botões de copiar/compartilhar. Quem publica no canal é ele
(modo semiautomático): o app NÃO posta no WhatsApp.

Decisões de arquitetura:
- Acesso por LINK SECRETO (/p/<token>/). Sem login nem senha para lembrar;
  quem não tem o link recebe 404, como se o app nem existisse.
- Gerar uma peça leva ~1 minuto. Em vez de segurar a requisição aberta,
  o servidor cria um "trabalho", devolve o id na hora, e a página consulta
  o progresso a cada segundo (polling). Simples e funciona em qualquer rede.
- Um único trabalhador em segundo plano: o modelo de recorte usa muita
  memória, então as peças entram numa fila e são feitas uma de cada vez.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from pipeline import (
    ERROS_DO_PIPELINE,
    ETAPAS,
    LOJAS,
    PASTA_SAIDA,
    Oferta,
    Pipeline,
    Produto,
    ProdutoInvalidoError,
    converter_preco,
)

log = logging.getLogger("web")

PASTA_ESTATICA = Path(__file__).parent / "static"
# Cada peça gerada pelo app ganha uma cópia própria da imagem. Sem isso,
# gerar o mesmo produto de novo sobrescreveria a imagem da peça anterior.
PASTA_PECAS_WEB = PASTA_SAIDA / "web"

# Quantas peças o histórico guarda na memória (as mais antigas saem).
LIMITE_HISTORICO = 50


# ---------------------------------------------------------------------- #
# Configuração do link secreto
# ---------------------------------------------------------------------- #

def obter_token() -> str:
    """
    Lê o token do .env (APP_TOKEN). Se não houver, gera um temporário:
    o app funciona, mas o link muda a cada reinício (bom para testar,
    ruim para o Danilo, por isso o README manda configurar).
    """
    token = os.getenv("APP_TOKEN", "").strip()
    if token:
        return token
    token = secrets.token_urlsafe(16)
    log.warning("APP_TOKEN não configurado; usando um link temporário.")
    return token


# ---------------------------------------------------------------------- #
# Trabalhos (uma peça sendo gerada)
# ---------------------------------------------------------------------- #

@dataclass
class Trabalho:
    id: str
    produto: Produto
    criado_em: float = field(default_factory=time.time)
    estado: str = "fila"  # fila -> processando -> pronto | erro
    etapa: int = 0
    descricao: str = "Na fila"
    nome_produto: str | None = None
    mensagens: dict[str, str] = field(default_factory=dict)
    imagem: Path | None = None
    erro: str | None = None

    def para_json(self) -> dict:
        return {
            "id": self.id,
            "estado": self.estado,
            "etapa": self.etapa,
            "total_etapas": len(ETAPAS),
            "descricao": self.descricao,
            "nome_produto": self.nome_produto,
            # Lista (e não dicionário) para manter a ordem das lojas na tela.
            "mensagens": [
                {"loja": loja, "nome_loja": LOJAS[loja], "texto": texto}
                for loja, texto in self.mensagens.items()
            ],
            # O id na URL evita que o navegador mostre uma imagem antiga do cache.
            "url_imagem": f"api/pecas/{self.id}/imagem" if self.imagem else None,
            "erro": self.erro,
            "entrada": {
                "link_amazon": self.produto.link_amazon,
                "ofertas": [
                    {"loja": o.loja, "nome_loja": LOJAS[o.loja], "preco_por": o.preco_por}
                    for o in self.produto.ofertas
                ],
            },
        }


class Fabrica:
    """
    Fila de trabalhos + o pipeline. Separada das rotas para poder ser
    testada (e trocada por uma versão falsa) sem subir servidor nenhum.
    """

    def __init__(self, criar_pipeline=Pipeline, pasta_pecas: Path = PASTA_PECAS_WEB) -> None:
        self._criar_pipeline = criar_pipeline
        self._pipeline = None
        self._pasta_pecas = pasta_pecas
        self._trabalhos: dict[str, Trabalho] = {}
        self._trava = threading.Lock()
        # max_workers=1: uma peça por vez (ver docstring do módulo).
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="peca")

    def aquecer(self) -> None:
        """
        Cria o pipeline em segundo plano assim que o app sobe, para o
        primeiro clique do Danilo não pagar o tempo de inicialização.
        """
        self._executor.submit(self._garantir_pipeline)

    def _garantir_pipeline(self):
        if self._pipeline is None:
            self._pipeline = self._criar_pipeline()
        return self._pipeline

    def criar(self, produto: Produto) -> Trabalho:
        trabalho = Trabalho(id=uuid.uuid4().hex[:12], produto=produto)
        with self._trava:
            self._trabalhos[trabalho.id] = trabalho
            self._limitar_historico()
        self._executor.submit(self._executar, trabalho)
        return trabalho

    def obter(self, id_trabalho: str) -> Trabalho | None:
        return self._trabalhos.get(id_trabalho)

    def listar(self) -> list[Trabalho]:
        with self._trava:
            return sorted(self._trabalhos.values(), key=lambda t: t.criado_em, reverse=True)

    def encerrar(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _limitar_historico(self) -> None:
        excesso = len(self._trabalhos) - LIMITE_HISTORICO
        for antigo in sorted(self._trabalhos.values(), key=lambda t: t.criado_em)[:max(excesso, 0)]:
            del self._trabalhos[antigo.id]

    def _executar(self, trabalho: Trabalho) -> None:
        trabalho.estado = "processando"

        def ao_avancar(numero: int, descricao: str) -> None:
            trabalho.etapa, trabalho.descricao = numero, descricao

        try:
            trabalho.descricao = "Preparando"
            peca = self._garantir_pipeline().processar(trabalho.produto, ao_avancar)
            self._pasta_pecas.mkdir(parents=True, exist_ok=True)
            copia = self._pasta_pecas / f"{trabalho.id}.png"
            shutil.copyfile(peca.caminho_imagem, copia)
        except ERROS_DO_PIPELINE as erro:
            trabalho.estado, trabalho.erro = "erro", str(erro)
        except Exception:
            # Bug de verdade: registra o traceback completo no servidor,
            # mas mostra uma mensagem amigável na tela.
            log.exception("Erro inesperado no trabalho %s", trabalho.id)
            trabalho.estado = "erro"
            trabalho.erro = "Erro inesperado. Tente de novo; se repetir, avise o Bruce."
        else:
            trabalho.nome_produto = peca.nome_produto
            trabalho.mensagens = peca.mensagens
            trabalho.imagem = copia
            trabalho.descricao = "Pronto"
            trabalho.estado = "pronto"


# ---------------------------------------------------------------------- #
# API
# ---------------------------------------------------------------------- #

class PedidoOferta(BaseModel):
    """Os campos de uma loja no formulário. Preços chegam como texto ("169,90")."""

    link: str = ""
    preco_por: str = ""
    preco_de: str = ""
    cupom: str = ""

    def preenchida(self) -> bool:
        return any(v.strip() for v in (self.link, self.preco_por, self.preco_de, self.cupom))


class PedidoPeca(BaseModel):
    """O que a interface envia: o link da Amazon e os campos de cada loja."""

    link_amazon: str
    ofertas: dict[str, PedidoOferta] = {}


def normalizar_link(link: str) -> str:
    """Completa o "https://" que muita gente esquece ao copiar o link."""
    link = link.strip()
    if link and "." in link and " " not in link and "://" not in link:
        link = f"https://{link}"
    return link


def montar_oferta(loja: str, pedido: PedidoOferta) -> Oferta:
    nome = LOJAS[loja]
    link = normalizar_link(pedido.link)
    if link and not link.startswith(("http://", "https://")):
        raise ProdutoInvalidoError(f"{nome}: o link não parece um endereço válido.")
    if not link and loja != "amazon":
        raise ProdutoInvalidoError(f"{nome}: cole o seu link de afiliado.")
    if not pedido.preco_por.strip():
        raise ProdutoInvalidoError(f"{nome}: informe o preço POR.")
    try:
        return Oferta(
            loja=loja,
            link=link or None,
            preco_por=converter_preco(pedido.preco_por),
            preco_de=converter_preco(pedido.preco_de) if pedido.preco_de.strip() else None,
            cupom=pedido.cupom.strip().upper() or None,
        )
    except ProdutoInvalidoError as erro:
        raise ProdutoInvalidoError(f"{nome}: {erro}") from erro


def montar_produto(pedido: PedidoPeca) -> Produto:
    """Valida o formulário e converte para o Produto do pipeline."""
    link_amazon = normalizar_link(pedido.link_amazon)
    if not link_amazon.startswith(("http://", "https://")):
        raise ProdutoInvalidoError("Cole o link do produto na Amazon (ex.: https://amzn.to/...).")

    lojas_desconhecidas = set(pedido.ofertas) - set(LOJAS)
    if lojas_desconhecidas:
        raise ProdutoInvalidoError(f"Loja desconhecida: {', '.join(sorted(lojas_desconhecidas))}.")

    # Só entram as lojas que o operador preencheu, na ordem fixa de LOJAS.
    ofertas = tuple(
        montar_oferta(loja, pedido.ofertas[loja])
        for loja in LOJAS
        if loja in pedido.ofertas and pedido.ofertas[loja].preenchida()
    )
    if not ofertas:
        raise ProdutoInvalidoError("Preencha o preço de pelo menos uma loja.")
    return Produto(link_amazon=link_amazon, ofertas=ofertas)


def criar_app(token: str | None = None, fabrica: Fabrica | None = None) -> FastAPI:
    """
    Fábrica do app (padrão "application factory"): os testes criam um app
    com token conhecido e uma fábrica falsa, sem tocar no código das rotas.
    """
    token = token or obter_token()
    fabrica = fabrica or Fabrica()

    @asynccontextmanager
    async def ciclo_de_vida(_app: FastAPI):
        fabrica.aquecer()
        log.info("App pronto.")
        yield
        fabrica.encerrar()

    app = FastAPI(title="Robô de Achadinhos", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=ciclo_de_vida)

    def checar_token(chave: str) -> None:
        # compare_digest leva o mesmo tempo acertando ou errando: impede
        # descobrir o token "medindo" quanto o servidor demora para responder.
        if not secrets.compare_digest(chave, token):
            raise HTTPException(status_code=404)

    def trabalho_ou_404(id_trabalho: str) -> Trabalho:
        trabalho = fabrica.obter(id_trabalho)
        if trabalho is None:
            raise HTTPException(status_code=404, detail="Peça não encontrada.")
        return trabalho

    protegido = [Depends(checar_token)]

    @app.get("/p/{chave}", dependencies=protegido, include_in_schema=False)
    def sem_barra(chave: str):
        # Os caminhos da página são relativos; precisam da barra no final.
        return RedirectResponse(f"/p/{chave}/")

    @app.get("/p/{chave}/", dependencies=protegido)
    def pagina(chave: str):
        return FileResponse(PASTA_ESTATICA / "index.html",
                            headers={"Cache-Control": "no-store"})

    @app.get("/p/{chave}/static/{arquivo}", dependencies=protegido)
    def estatico(chave: str, arquivo: str):
        caminho = (PASTA_ESTATICA / arquivo).resolve()
        # Impede "../" para ler arquivos fora da pasta estática.
        if caminho.parent != PASTA_ESTATICA.resolve() or not caminho.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(caminho, headers={"Cache-Control": "no-cache"})

    @app.post("/p/{chave}/api/pecas", dependencies=protegido, status_code=202)
    def criar_peca(chave: str, pedido: PedidoPeca):
        try:
            produto = montar_produto(pedido)
        except ProdutoInvalidoError as erro:
            raise HTTPException(status_code=422, detail=str(erro)) from erro
        return fabrica.criar(produto).para_json()

    @app.post("/p/{chave}/api/pecas/{id_trabalho}/refazer", dependencies=protegido,
              status_code=202)
    def refazer_peca(chave: str, id_trabalho: str):
        # Gera outra versão com os mesmos dados que já estão no servidor.
        return fabrica.criar(trabalho_ou_404(id_trabalho).produto).para_json()

    @app.get("/p/{chave}/api/pecas", dependencies=protegido)
    def listar_pecas(chave: str):
        return [t.para_json() for t in fabrica.listar()]

    @app.get("/p/{chave}/api/pecas/{id_trabalho}", dependencies=protegido)
    def ver_peca(chave: str, id_trabalho: str):
        return trabalho_ou_404(id_trabalho).para_json()

    @app.get("/p/{chave}/api/pecas/{id_trabalho}/imagem", dependencies=protegido)
    def imagem_peca(chave: str, id_trabalho: str):
        trabalho = trabalho_ou_404(id_trabalho)
        if trabalho.imagem is None:
            raise HTTPException(status_code=404, detail="A imagem ainda não está pronta.")
        nome = f"achadinho-{trabalho.id}.png"
        return FileResponse(trabalho.imagem, media_type="image/png", filename=nome,
                            content_disposition_type="inline")

    return app
