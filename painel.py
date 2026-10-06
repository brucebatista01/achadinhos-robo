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

import hashlib
import hmac
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

import robo
from services.whatsapp_service import WhatsAppError, WhatsAppService

load_dotenv()

PASTA_WEB = Path(__file__).parent / "painel_web"
# Números do WhatsApp guardados por 20 segundos: o painel atualiza sozinho a
# cada minuto, e abrir em várias abas ao mesmo tempo não multiplica os
# pedidos ao WhatsApp.
SEGUNDOS_CACHE_METRICAS = 20

# Errar o PIN 5 vezes bloqueia novas tentativas daquele endereço por 15 min:
# um PIN curto não aguenta um robô testando milhares de combinações.
TENTATIVAS_MAXIMAS = 5
SEGUNDOS_BLOQUEIO = 15 * 60
COOKIE = "painel_sessao"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
whatsapp = WhatsAppService()
_cache_metricas: dict = {"quando": 0.0, "dados": None}
_erros_de_pin: dict[str, list[float]] = {}


def _conferir_token(token: str) -> None:
    esperado = os.getenv("PAINEL_TOKEN", "")
    # compare_digest leva o mesmo tempo acertando ou errando, então não dá
    # para descobrir o código letra por letra medindo o tempo de resposta.
    if len(esperado) < 16 or not secrets.compare_digest(token, esperado):
        raise HTTPException(status_code=404)


def _assinatura_da_sessao() -> str:
    """
    O valor do "crachá" (cookie) de quem acertou o PIN.

    É uma assinatura do PIN feita com o código secreto do link: não dá para
    fabricar sem conhecer os dois, e trocar o PIN ou o link no .env derruba
    todo mundo que estava dentro.
    """
    chave = os.getenv("PAINEL_TOKEN", "").encode()
    return hmac.new(chave, os.getenv("PAINEL_PIN", "").encode(), hashlib.sha256).hexdigest()


def _conferir_acesso(token: str, request: Request) -> None:
    """Link certo E PIN já digitado (cookie válido). Sem isso, nada passa."""
    _conferir_token(token)
    if not os.getenv("PAINEL_PIN"):
        return  # sem PIN configurado, o link sozinho dá acesso
    if not secrets.compare_digest(request.cookies.get(COOKIE, ""), _assinatura_da_sessao()):
        raise HTTPException(status_code=401, detail="Digite o PIN.")


def _bloqueado(endereco: str) -> bool:
    agora_ = time.time()
    recentes = [t for t in _erros_de_pin.get(endereco, []) if agora_ - t < SEGUNDOS_BLOQUEIO]
    _erros_de_pin[endereco] = recentes
    return len(recentes) >= TENTATIVAS_MAXIMAS


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


def _ler_legenda(legenda: str) -> dict:
    """
    Para posts sem registro do robô (feitos antes do painel existir, ou à
    mão), tira produto e link do próprio texto do post, que segue o padrão
    "✅ Nome" e "🔗 link".
    """
    produto = next((l[1:].strip() for l in legenda.splitlines() if l.startswith("✅")), None)
    link = next((l[1:].strip() for l in legenda.splitlines() if l.startswith("🔗")), "")
    asin = link.split("/dp/")[1][:10] if "/dp/" in link else None
    imagem = f"{asin}_final.png" if asin and (robo.PASTA_PECAS / f"{asin}_final.png").exists() else None
    return {
        "produto": produto or (legenda.split("\n")[0][:60] or "Post"),
        "asin": asin,
        "imagem": imagem,
        "linha": link.split("?")[0] if link else None,
    }


# ---------------------------------------------------------------------- #
# Páginas
# ---------------------------------------------------------------------- #

@app.get("/p/{token}/", response_class=HTMLResponse)
def pagina(token: str, request: Request) -> str:
    _conferir_token(token)
    try:
        _conferir_acesso(token, request)
    except HTTPException:
        return (PASTA_WEB / "pin.html").read_text(encoding="utf-8")
    return (PASTA_WEB / "index.html").read_text(encoding="utf-8")


@app.post("/p/{token}/entrar")
async def entrar(token: str, request: Request) -> JSONResponse:
    _conferir_token(token)
    endereco = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
    if _bloqueado(endereco):
        raise HTTPException(status_code=429, detail="Muitas tentativas erradas. Espere 15 minutos.")
    pin = str((await request.json()).get("pin", "")).strip().upper()
    if not secrets.compare_digest(pin, os.getenv("PAINEL_PIN", "").upper()):
        _erros_de_pin.setdefault(endereco, []).append(time.time())
        raise HTTPException(status_code=403, detail="PIN errado.")
    resposta = JSONResponse({"ok": True})
    resposta.set_cookie(
        COOKIE, _assinatura_da_sessao(), max_age=30 * 24 * 3600, httponly=True,
        samesite="strict", secure=request.headers.get("x-forwarded-proto") == "https",
        path=f"/p/{token}/",
    )
    return resposta


@app.post("/p/{token}/sair")
def sair(token: str) -> JSONResponse:
    _conferir_token(token)
    resposta = JSONResponse({"ok": True})
    resposta.delete_cookie(COOKIE, path=f"/p/{token}/")
    return resposta


@app.get("/p/{token}/imagem/{nome}")
def imagem(token: str, nome: str, request: Request) -> FileResponse:
    _conferir_acesso(token, request)
    caminho = (robo.PASTA_PECAS / nome).resolve()
    # Só entrega arquivos de dentro da pasta das peças (nada de "../.env").
    if caminho.parent != robo.PASTA_PECAS.resolve() or not caminho.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(caminho)


# ---------------------------------------------------------------------- #
# API do painel
# ---------------------------------------------------------------------- #

@app.get("/p/{token}/api/resumo")
def resumo(token: str, request: Request) -> dict:
    _conferir_acesso(token, request)
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
def metricas(token: str, request: Request, atualizar: bool = False) -> dict:
    """
    Junta o que o WhatsApp sabe (visualizações, reações) com o que o robô
    sabe (qual produto foi em cada post) e calcula os destaques.
    """
    _conferir_acesso(token, request)
    agora_ = time.time()
    if atualizar or _cache_metricas["dados"] is None or agora_ - _cache_metricas["quando"] > SEGUNDOS_CACHE_METRICAS:
        try:
            _cache_metricas["dados"] = whatsapp.metricas(_canal(), limite=100)
            _cache_metricas["quando"] = agora_
        except WhatsAppError as erro:
            if _cache_metricas["dados"] is None:
                raise HTTPException(status_code=503, detail=f"WhatsApp indisponível: {erro}") from erro
            # Mostra os últimos números que deram certo, avisando que são velhos.
            _cache_metricas["dados"] = {**_cache_metricas["dados"], "erroServidor": str(erro)}
    dados = _cache_metricas["dados"]

    nossos = {post["server_id"]: post for post in _ler_posts() if post.get("server_id")}
    posts = []
    for numeros in dados.get("posts", []):
        nosso = nossos.get(numeros["serverId"]) or _ler_legenda(numeros.get("legenda") or "")
        posts.append({
            **numeros,
            "produto": nosso.get("produto"),
            "asin": nosso.get("asin"),
            "imagem": nosso.get("imagem"),
            "link": _link_do_post(nosso["linha"]) if nosso.get("linha") else None,
            "do_robo": "server_id" in nosso,
        })
    posts.sort(key=lambda post: post["quando"], reverse=True)
    return {
        "seguidores": dados.get("seguidores"),
        "atualizado_em": datetime.fromtimestamp(_cache_metricas["quando"], robo.BRASILIA).strftime("%H:%M:%S"),
        "aviso": "O WhatsApp não respondeu agora; números podem estar atrasados." if dados.get("erroServidor") else None,
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
def ligar(token: str, request: Request) -> dict:
    _conferir_acesso(token, request)
    estado = robo.Estado.carregar()
    estado.ligado, estado.motivo = True, ""
    estado.salvar()
    return {"ok": True}


@app.post("/p/{token}/api/desligar")
def desligar(token: str, request: Request) -> dict:
    _conferir_acesso(token, request)
    estado = robo.Estado.carregar()
    estado.ligado, estado.motivo = False, ""
    estado.salvar()
    return {"ok": True}


@app.post("/p/{token}/api/postar-agora")
def postar_agora(token: str, request: Request) -> dict:
    _conferir_acesso(token, request)
    robo.ARQUIVO_POSTAR_AGORA.touch()
    return {"ok": True, "aviso": "O post sai em até 1 minuto."}


@app.post("/p/{token}/api/config")
async def salvar_config(token: str, request: Request) -> dict:
    _conferir_acesso(token, request)
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
def ler_lista_texto(token: str, request: Request) -> dict:
    _conferir_acesso(token, request)
    texto = robo.ARQUIVO_LISTA.read_text(encoding="utf-8") if robo.ARQUIVO_LISTA.exists() else ""
    return {"texto": texto}


@app.post("/p/{token}/api/lista")
async def salvar_lista(token: str, request: Request) -> dict:
    """Confere a lista inteira antes de gravar: lista com erro não substitui a boa."""
    _conferir_acesso(token, request)
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
