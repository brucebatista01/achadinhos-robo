"""
Painel do robô: o "app" do dono, acessado por um link secreto.

    https://<endereço>/p/<PAINEL_TOKEN>/

Mostra se o robô está ligado, os números do canal (seguidores e, por post,
visualizações e reações) e deixa ligar/desligar, mudar o intervalo e o
horário, postar na hora e editar a lista de produtos.

Por que um link secreto e não login com senha: o dono só precisa guardar um
link. Quem não tem o código recebe "não encontrado", como se o painel nem
existisse. O código fica no .env (PAINEL_TOKEN) e pode ser trocado a qualquer
momento, o que derruba o link antigo.

O painel não posta nada sozinho: ele só grava arquivos em `dados/` (estado,
config, pedido de "postar agora", lista) que o robô (robo.py) relê a cada
30 segundos. Assim os dois podem reiniciar separados sem perder nada.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

import robo
from services.whatsapp_service import WhatsAppError, WhatsAppService

load_dotenv()

PASTA_WEB = Path(__file__).parent / "painel_web"
# Números do WhatsApp guardados por 2 minutos: abrir o painel várias vezes
# não deve ficar pedindo tudo de novo ao WhatsApp.
SEGUNDOS_CACHE_METRICAS = 120

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
whatsapp = WhatsAppService()
_cache_metricas: dict = {"quando": 0.0, "dados": None}


def _conferir_token(token: str) -> None:
    esperado = os.getenv("PAINEL_TOKEN", "")
    # compare_digest leva o mesmo tempo acertando ou errando, então não dá
    # para descobrir o código letra por letra medindo o tempo de resposta.
    if len(esperado) < 16 or not secrets.compare_digest(token, esperado):
        raise HTTPException(status_code=404)


def _canal() -> str:
    return os.getenv("WHATSAPP_CANAL", "")


def _ler_posts() -> list[dict]:
    if not robo.ARQUIVO_POSTS.exists():
        return []
    posts = []
    for linha in robo.ARQUIVO_POSTS.read_text(encoding="utf-8").splitlines():
        try:
            posts.append(json.loads(linha))
        except ValueError:
            continue
    return posts


def _link_do_post(linha: str) -> str:
    return linha.split("|")[0].strip()


# ---------------------------------------------------------------------- #
# Páginas
# ---------------------------------------------------------------------- #

@app.get("/p/{token}/", response_class=HTMLResponse)
def pagina(token: str) -> str:
    _conferir_token(token)
    return (PASTA_WEB / "index.html").read_text(encoding="utf-8")


@app.get("/p/{token}/imagem/{nome}")
def imagem(token: str, nome: str) -> FileResponse:
    _conferir_token(token)
    caminho = (robo.PASTA_PECAS / nome).resolve()
    # Só entrega arquivos de dentro da pasta das peças (nada de "../.env").
    if caminho.parent != robo.PASTA_PECAS.resolve() or not caminho.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(caminho)


# ---------------------------------------------------------------------- #
# API do painel
# ---------------------------------------------------------------------- #

@app.get("/p/{token}/api/resumo")
def resumo(token: str) -> dict:
    _conferir_token(token)
    estado, config = robo.Estado.carregar(), robo.Config.carregar()
    posts = _ler_posts()
    hoje = robo.agora().date().isoformat()
    try:
        total_lista = len(robo.ler_lista())
    except robo.RoboError:
        total_lista = 0
    cache = json.loads(robo.ARQUIVO_CACHE.read_text(encoding="utf-8")) if robo.ARQUIVO_CACHE.exists() else {}
    return {
        "ligado": estado.ligado,
        "motivo": estado.motivo,
        "ultimo_em": estado.ultimo_em,
        "config": config.__dict__,
        "canal": _canal(),
        "whatsapp_pronto": whatsapp.esta_pronto(),
        "dentro_do_horario": robo.dentro_do_horario(robo.agora(), config),
        "produtos_na_lista": total_lista,
        "pecas_prontas": len(cache),
        "posts_total": len(posts),
        "posts_hoje": sum(1 for post in posts if post.get("quando", "").startswith(hoje)),
        "afiliado": os.getenv("AMAZON_TAG", ""),
    }


@app.get("/p/{token}/api/metricas")
def metricas(token: str, atualizar: bool = False) -> dict:
    """
    Junta o que o WhatsApp sabe (visualizações, reações) com o que o robô
    sabe (qual produto foi em cada post) e calcula os destaques.
    """
    _conferir_token(token)
    agora_ = time.time()
    if atualizar or _cache_metricas["dados"] is None or agora_ - _cache_metricas["quando"] > SEGUNDOS_CACHE_METRICAS:
        try:
            _cache_metricas["dados"] = whatsapp.metricas(_canal(), limite=100)
            _cache_metricas["quando"] = agora_
        except WhatsAppError as erro:
            if _cache_metricas["dados"] is None:
                raise HTTPException(status_code=503, detail=f"WhatsApp indisponível: {erro}") from erro
    dados = _cache_metricas["dados"]

    nossos = {post["server_id"]: post for post in _ler_posts() if post.get("server_id")}
    posts = []
    for numeros in dados.get("posts", []):
        nosso = nossos.get(numeros["serverId"], {})
        posts.append({
            **numeros,
            "produto": nosso.get("produto") or (numeros.get("legenda") or "").split("\n")[0][:60],
            "asin": nosso.get("asin"),
            "imagem": nosso.get("imagem"),
            "link": _link_do_post(nosso["linha"]) if nosso.get("linha") else None,
            "do_robo": bool(nosso),
        })
    posts.sort(key=lambda post: post["quando"], reverse=True)
    return {
        "seguidores": dados.get("seguidores"),
        "atualizado_em": datetime.fromtimestamp(_cache_metricas["quando"], robo.BRASILIA).strftime("%H:%M"),
        "posts": posts,
        "destaques": _destaques(posts),
    }


def _destaques(posts: list[dict]) -> dict:
    """Os números que ajudam a decidir: média, melhor horário, campeões."""
    if not posts:
        return {}
    visualizacoes = [post["visualizacoes"] for post in posts]
    por_hora: dict[int, list[int]] = defaultdict(list)
    for post in posts:
        hora = datetime.fromtimestamp(post["quando"], robo.BRASILIA).hour
        por_hora[hora].append(post["visualizacoes"])
    media_por_hora = {hora: sum(v) / len(v) for hora, v in por_hora.items()}
    melhor_hora = max(media_por_hora, key=media_por_hora.get)
    campeoes = sorted(posts, key=lambda post: (post["visualizacoes"], post["reacoes"]), reverse=True)[:5]
    repetidos = Counter(post["produto"] for post in posts if post["do_robo"])
    return {
        "media_visualizacoes": round(sum(visualizacoes) / len(visualizacoes), 1),
        "total_reacoes": sum(post["reacoes"] for post in posts),
        "total_encaminhamentos": sum(post["encaminhamentos"] for post in posts),
        "melhor_hora": melhor_hora,
        "media_por_hora": {str(h): round(v, 1) for h, v in sorted(media_por_hora.items())},
        "campeoes": [post["serverId"] for post in campeoes],
        "mais_repetido": repetidos.most_common(1)[0] if repetidos else None,
    }


@app.post("/p/{token}/api/ligar")
def ligar(token: str) -> dict:
    _conferir_token(token)
    estado = robo.Estado.carregar()
    estado.ligado, estado.motivo = True, ""
    estado.salvar()
    return {"ok": True}


@app.post("/p/{token}/api/desligar")
def desligar(token: str) -> dict:
    _conferir_token(token)
    estado = robo.Estado.carregar()
    estado.ligado, estado.motivo = False, ""
    estado.salvar()
    return {"ok": True}


@app.post("/p/{token}/api/postar-agora")
def postar_agora(token: str) -> dict:
    _conferir_token(token)
    robo.ARQUIVO_POSTAR_AGORA.touch()
    return {"ok": True, "aviso": "O post sai em até 1 minuto."}


@app.post("/p/{token}/api/config")
async def salvar_config(token: str, request: Request) -> dict:
    _conferir_token(token)
    corpo = await request.json()
    try:
        config = robo.Config(
            minutos_entre_posts=int(corpo["minutos_entre_posts"]),
            hora_inicio=int(corpo["hora_inicio"]),
            hora_fim=int(corpo["hora_fim"]),
        )
        config.salvar()
    except (KeyError, ValueError) as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from erro
    return {"ok": True, "config": config.__dict__}


@app.get("/p/{token}/api/lista")
def ler_lista_texto(token: str) -> dict:
    _conferir_token(token)
    texto = robo.ARQUIVO_LISTA.read_text(encoding="utf-8") if robo.ARQUIVO_LISTA.exists() else ""
    return {"texto": texto}


@app.post("/p/{token}/api/lista")
async def salvar_lista(token: str, request: Request) -> dict:
    """Confere a lista inteira antes de gravar: lista com erro não substitui a boa."""
    _conferir_token(token)
    texto = (await request.json()).get("texto", "")
    provisoria = robo.ARQUIVO_LISTA.with_suffix(".novo")
    provisoria.write_text(texto, encoding="utf-8")
    try:
        total = len(robo.ler_lista(provisoria))
    except robo.RoboError as erro:
        provisoria.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(erro)) from erro
    provisoria.replace(robo.ARQUIVO_LISTA)
    return {"ok": True, "produtos": total}


if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))
