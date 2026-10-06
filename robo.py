"""
Robô automático: a cada X minutos sorteia um produto da lista, gera a peça
(imagem + texto) e posta no canal do WhatsApp.

Comandos:
    python robo.py rodar      # fica rodando (é o que o Docker executa)
    python robo.py ligar      # volta a postar
    python robo.py desligar   # para de postar (o processo continua de pé)
    python robo.py status     # mostra se está ligado e o último post
    python robo.py agora      # posta um produto agora, para testar

Por que "ligar/desligar" é um arquivo e não parar o processo: no Docker o
processo reinicia sozinho se cair. Então o robô fica sempre de pé e só
consulta o arquivo `dados/estado.json` para saber se deve postar. Isso também
deixa o robô se DESLIGAR sozinho quando um link falha, e esperar alguém
corrigir a lista e ligar de novo.

ATENÇÃO: postar em canal do WhatsApp não tem API oficial. Use só com o
número de teste: ele pode ser banido.
"""

from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from main import configurar_log, interpretar_linha
from pipeline import ERROS_DO_PIPELINE, Pipeline, Produto, ProdutoInvalidoError
from services.whatsapp_service import WhatsAppError, WhatsAppService

load_dotenv()

PASTA_DADOS = Path(os.getenv("PASTA_DADOS", "dados"))
ARQUIVO_LISTA = PASTA_DADOS / "lista.txt"
ARQUIVO_ESTADO = PASTA_DADOS / "estado.json"
ARQUIVO_CACHE = PASTA_DADOS / "pecas.json"
ARQUIVO_HISTORICO = PASTA_DADOS / "postados.log"
# Mesmo histórico, em formato que o painel lê (um JSON por linha).
ARQUIVO_POSTS = PASTA_DADOS / "postados.jsonl"
ARQUIVO_CONFIG = PASTA_DADOS / "config.json"
# Se este arquivo existir, o robô posta na próxima conferida (botão do painel).
ARQUIVO_POSTAR_AGORA = PASTA_DADOS / "postar_agora"
PASTA_PECAS = PASTA_DADOS / "output"

MINUTOS_ENTRE_POSTS = int(os.getenv("MINUTOS_ENTRE_POSTS", "15"))
# Só posta dentro deste horário (de Brasília). Post de madrugada ninguém vê,
# e um robô que posta 24h por dia chama mais atenção do WhatsApp.
HORA_INICIO = int(os.getenv("HORA_INICIO", "8"))
HORA_FIM = int(os.getenv("HORA_FIM", "23"))
# Brasil não tem mais horário de verão: UTC-3 fixo dispensa base de fusos.
BRASILIA = timezone(timedelta(hours=-3))

SEGUNDOS_ENTRE_CONFERIDAS = 30

log = logging.getLogger("robo")


class RoboError(Exception):
    """Problema que exige a mão de uma pessoa (o robô se desliga e avisa)."""


# ---------------------------------------------------------------------- #
# Estado (ligado/desligado) e histórico
# ---------------------------------------------------------------------- #

@dataclass
class Estado:
    ligado: bool = True
    motivo: str = ""          # por que foi desligado (vazio = desligado à mão)
    ultimo: str = ""          # linha do último produto postado
    ultimo_em: str = ""       # quando (texto, horário de Brasília)

    @classmethod
    def carregar(cls) -> Estado:
        caminho = ARQUIVO_ESTADO
        if not caminho.exists():
            return cls()
        return cls(**json.loads(caminho.read_text(encoding="utf-8")))

    def salvar(self) -> None:
        caminho = ARQUIVO_ESTADO
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(json.dumps(self.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass
class Config:
    """
    Ajustes que o dono muda pelo painel, sem reiniciar nada.

    O .env dá os valores iniciais; o painel grava `dados/config.json`, que
    vale por cima deles. O robô relê a cada conferida.
    """

    minutos_entre_posts: int = MINUTOS_ENTRE_POSTS
    hora_inicio: int = HORA_INICIO
    hora_fim: int = HORA_FIM

    @classmethod
    def carregar(cls) -> Config:
        if not ARQUIVO_CONFIG.exists():
            return cls()
        salvos = json.loads(ARQUIVO_CONFIG.read_text(encoding="utf-8"))
        return cls(**{chave: int(valor) for chave, valor in salvos.items() if chave in cls.__dataclass_fields__})

    def validar(self) -> None:
        if not 1 <= self.minutos_entre_posts <= 24 * 60:
            raise ValueError("O intervalo precisa ficar entre 1 minuto e 24 horas.")
        if not (0 <= self.hora_inicio < self.hora_fim <= 24):
            raise ValueError("O horário precisa ser algo como 8h às 23h (início antes do fim).")

    def salvar(self) -> None:
        self.validar()
        ARQUIVO_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        ARQUIVO_CONFIG.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")


def agora() -> datetime:
    return datetime.now(BRASILIA)


def registrar_post(linha: str, detalhes: dict | None = None) -> None:
    """
    Guarda o post em dois formatos: uma linha de texto fácil de ler e um JSON
    com os detalhes (produto, imagem, número do post no WhatsApp) para o
    painel cruzar com as visualizações.
    """
    caminho = ARQUIVO_HISTORICO
    caminho.parent.mkdir(parents=True, exist_ok=True)
    momento = agora()
    with caminho.open("a", encoding="utf-8") as arquivo:
        arquivo.write(f"{momento:%d/%m/%Y %H:%M} | {linha}\n")
    registro = {"quando": momento.isoformat(timespec="seconds"), "linha": linha, **(detalhes or {})}
    with ARQUIVO_POSTS.open("a", encoding="utf-8") as arquivo:
        arquivo.write(json.dumps(registro, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------- #
# Lista de produtos e sorteio
# ---------------------------------------------------------------------- #

def ler_lista(caminho: Path | None = None) -> list[tuple[str, Produto]]:
    """
    Lê a lista (mesmo formato do produtos.txt: link | preco_por | preco_de | cupom).

    Diferente do lote, uma linha mal escrita aqui PARA o robô: ninguém está
    olhando o terminal, então é melhor parar e avisar do que pular calado.
    """
    caminho = caminho or ARQUIVO_LISTA
    if not caminho.exists():
        raise RoboError(f"Lista de produtos não encontrada ({caminho}).")
    produtos = []
    for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), 1):
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        try:
            produtos.append((linha, com_etiqueta_de_afiliado(interpretar_linha(linha))))
        except ProdutoInvalidoError as erro:
            raise RoboError(f"Linha {numero} da lista está errada: {erro}") from erro
    if not produtos:
        raise RoboError("A lista de produtos está vazia.")
    return produtos


def com_etiqueta_de_afiliado(produto: Produto) -> Produto:
    """
    Com AMAZON_TAG no .env, TODO post usa a etiqueta de afiliado da dona do
    robô, mesmo que o link da lista seja de outra pessoa (ex.: amzn.to de
    outro afiliado). Apagar o link da oferta faz o pipeline montar o link
    com a etiqueta a partir do código do produto (ASIN).
    """
    if not os.getenv("AMAZON_TAG"):
        return produto
    ofertas = tuple(replace(oferta, link=None) for oferta in produto.ofertas)
    return replace(produto, ofertas=ofertas)


def sortear(produtos: list[tuple[str, Produto]], ultimo: str,
            sorteio: random.Random | None = None) -> tuple[str, Produto]:
    """Escolhe um produto ao acaso, sem repetir o último postado."""
    sorteio = sorteio or random.Random()
    opcoes = [item for item in produtos if item[0] != ultimo] or produtos
    return sorteio.choice(opcoes)


def dentro_do_horario(momento: datetime, config: Config | None = None) -> bool:
    config = config or Config()
    return config.hora_inicio <= momento.hour < config.hora_fim


# ---------------------------------------------------------------------- #
# O robô
# ---------------------------------------------------------------------- #

@dataclass
class Robo:
    """
    Junta o pipeline (gera a peça) e o WhatsApp (posta).

    As peças ficam guardadas por linha da lista: quando o mesmo produto é
    sorteado de novo, reaproveita a imagem em vez de gerar outra. Isso economiza
    crédito (a chave grátis do PhotoRoom tem limite por mês). Se a linha mudar
    (preço, cupom), a peça é gerada de novo.
    """

    canal: str
    whatsapp: WhatsAppService = field(default_factory=WhatsAppService)
    _pipeline: Pipeline | None = None

    def _gerar_ou_reaproveitar(self, linha: str, produto: Produto) -> tuple[Path, str, dict]:
        cache = json.loads(ARQUIVO_CACHE.read_text(encoding="utf-8")) if ARQUIVO_CACHE.exists() else {}
        chave = f"{linha} [tag={os.getenv('AMAZON_TAG', '')}]"
        guardada = cache.get(chave)
        if guardada and Path(guardada["imagem"]).exists():
            log.info("  Reaproveitando a peça já gerada.")
            return Path(guardada["imagem"]), guardada["texto"], guardada.get("detalhes", {})

        if self._pipeline is None:  # carrega o modelo de recorte só quando precisa
            self._pipeline = Pipeline(pasta_saida=PASTA_PECAS)
        peca = self._pipeline.processar(
            produto, ao_avancar=lambda n, etapa: log.info("  [%d/6] %s...", n, etapa)
        )
        texto = peca.mensagens["amazon"]
        detalhes = {"asin": peca.asin, "produto": peca.nome_produto}
        cache[chave] = {"imagem": str(peca.caminho_imagem), "texto": texto, "detalhes": detalhes}
        ARQUIVO_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        return peca.caminho_imagem, texto, detalhes

    def postar_um(self, estado: Estado) -> None:
        """Sorteia, gera e posta UM produto. Qualquer falha vira RoboError."""
        linha, produto = sortear(ler_lista(), estado.ultimo)
        log.info("Sorteado: %s", linha)
        try:
            imagem, texto, detalhes = self._gerar_ou_reaproveitar(linha, produto)
        except ERROS_DO_PIPELINE as erro:
            raise RoboError(f"Falhou ao gerar a peça de:\n{linha}\nMotivo: {erro}") from erro
        try:
            resultado = self.whatsapp.postar(self.canal, imagem, texto)
        except WhatsAppError as erro:
            raise RoboError(f"Falhou ao postar no WhatsApp: {erro}") from erro
        if not resultado.get("etiquetaIA"):
            log.warning("  Post sem a etiqueta de IA (o WhatsApp não aceitou). Marque à mão.")

        registrar_post(linha, {
            **detalhes,
            "imagem": imagem.name,
            "server_id": resultado.get("serverId"),
            "etiqueta_ia": bool(resultado.get("etiquetaIA")),
        })
        # Relê o estado antes de gravar: alguém pode ter desligado o robô
        # enquanto a peça era gerada, e isso não pode ser desfeito aqui.
        estado = Estado.carregar()
        estado.ultimo, estado.ultimo_em = linha, f"{agora():%d/%m/%Y %H:%M}"
        estado.salvar()
        log.info("  ✓ Postado no canal.")

    def desligar_e_avisar(self, estado: Estado, motivo: str) -> None:
        """Para de postar e manda o motivo para o próprio número de teste."""
        log.error("ROBÔ DESLIGADO: %s", motivo)
        estado.ligado, estado.motivo = False, motivo
        estado.salvar()
        try:
            self.whatsapp.avisar(
                f"⚠️ Robô de achadinhos PAROU.\n\n{motivo}\n\nCorrija e ligue de novo."
            )
        except WhatsAppError as erro:
            log.error("Também não consegui avisar no WhatsApp: %s", erro)

    def rodar(self) -> None:
        """Laço principal: confere o estado de tempos em tempos e posta na hora certa."""
        log.info("Robô de pé, no canal %r. Intervalo e horário vêm do painel.", self.canal)
        ultimo_post: datetime | None = None
        while True:
            estado, config, momento = Estado.carregar(), Config.carregar(), agora()
            pedido_do_painel = ARQUIVO_POSTAR_AGORA.exists()
            na_hora = ultimo_post is None or momento >= ultimo_post + timedelta(minutes=config.minutos_entre_posts)
            if pedido_do_painel or (estado.ligado and dentro_do_horario(momento, config) and na_hora):
                ARQUIVO_POSTAR_AGORA.unlink(missing_ok=True)
                if not self.whatsapp.esta_pronto():
                    log.info("Esperando o WhatsApp conectar...")
                else:
                    try:
                        self.postar_um(estado)
                    except RoboError as erro:
                        self.desligar_e_avisar(estado, str(erro))
                    ultimo_post = agora()
            time.sleep(SEGUNDOS_ENTRE_CONFERIDAS)


# ---------------------------------------------------------------------- #
# Linha de comando
# ---------------------------------------------------------------------- #

def main(argumentos: list[str]) -> int:
    configurar_log()
    comando = argumentos[0] if argumentos else "status"
    estado = Estado.carregar()

    if comando == "ligar":
        estado.ligado, estado.motivo = True, ""
        estado.salvar()
        print(f"Robô LIGADO. O próximo post sai em até {Config.carregar().minutos_entre_posts} minuto(s).")
    elif comando == "desligar":
        estado.ligado, estado.motivo = False, ""
        estado.salvar()
        print("Robô DESLIGADO. Nada mais será postado até ligar de novo.")
    elif comando == "status":
        print("Ligado" if estado.ligado else f"Desligado {estado.motivo and '- ' + estado.motivo}")
        print(f"Último post: {estado.ultimo_em or 'nenhum'} {estado.ultimo}")
    elif comando in ("rodar", "agora"):
        canal = os.getenv("WHATSAPP_CANAL")
        if not canal:
            print("Defina WHATSAPP_CANAL no .env (nome exato do canal).")
            return 1
        robo = Robo(canal=canal)
        if comando == "rodar":
            robo.rodar()
        else:
            try:
                robo.postar_um(estado)
            except RoboError as erro:
                print(f"Falhou: {erro}")
                return 2
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
