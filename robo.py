"""
Robô automático: a cada X minutos sorteia um produto da lista, gera a peça
(imagem + texto) e posta no canal do WhatsApp.

Comandos:
    python robo.py rodar      # fica rodando, para TODOS os clientes (é o que o Docker executa)
    python robo.py ligar      # volta a postar
    python robo.py desligar   # para de postar (o processo continua de pé)
    python robo.py status     # mostra se está ligado e o último post
    python robo.py agora      # posta um produto agora, para testar
    (ligar/desligar/status/agora aceitam --cliente <id>; sem ele, vale o principal)

Vários clientes (ver clientes.py): cada um roda numa "linha de execução"
(thread) própria, com seu canal, sua lista e seu horário. Gerar imagem nova
é feito por um cliente de cada vez (trava `_GERANDO`): o recorte e a visão
pesam, e assim a memória da VPS não estoura quando dois sorteiam juntos.

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
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

import clientes
from clientes import Cliente
from main import configurar_log, interpretar_linha
from pipeline import ERROS_DO_PIPELINE, Pipeline, Produto, ProdutoInvalidoError
from services.amazon_service import AmazonError, AmazonService
from services.message_service import MessageService
from services.whatsapp_service import WhatsAppError, WhatsAppService

load_dotenv()

# Os arquivos de cada cliente (lista, estado, config, peças, histórico) ficam
# na pasta dele: ver as propriedades de clientes.Cliente.

MINUTOS_ENTRE_POSTS = int(os.getenv("MINUTOS_ENTRE_POSTS", "15"))
# Só posta dentro deste horário (de Brasília). Post de madrugada ninguém vê,
# e um robô que posta 24h por dia chama mais atenção do WhatsApp.
HORA_INICIO = int(os.getenv("HORA_INICIO", "8"))
HORA_FIM = int(os.getenv("HORA_FIM", "23"))
# Brasil não tem mais horário de verão: UTC-3 fixo dispensa base de fusos.
BRASILIA = timezone(timedelta(hours=-3))

SEGUNDOS_ENTRE_CONFERIDAS = 30
# Antes de postar, confere o preço do momento na Amazon (1 = sim).
ATUALIZAR_PRECO = os.getenv("ATUALIZAR_PRECO", "1") == "1"

BuscarPreco = Callable[[str], tuple[float | None, float | None]]

# Um cliente gera imagem nova por vez (ver docstring do módulo).
_GERANDO = threading.Lock()

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
    def carregar(cls, cliente: Cliente) -> Estado:
        caminho = cliente.estado
        if not caminho.exists():
            return cls()
        return cls(**json.loads(caminho.read_text(encoding="utf-8")))

    def salvar(self, cliente: Cliente) -> None:
        caminho = cliente.estado
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
    def carregar(cls, cliente: Cliente) -> Config:
        if not cliente.config.exists():
            return cls()
        salvos = json.loads(cliente.config.read_text(encoding="utf-8"))
        return cls(**{chave: int(valor) for chave, valor in salvos.items() if chave in cls.__dataclass_fields__})

    def validar(self) -> None:
        if not 1 <= self.minutos_entre_posts <= 24 * 60:
            raise ValueError("O intervalo precisa ficar entre 1 minuto e 24 horas.")
        if not (0 <= self.hora_inicio < self.hora_fim <= 24):
            raise ValueError("O horário precisa ser algo como 8h às 23h (início antes do fim).")

    def salvar(self, cliente: Cliente) -> None:
        self.validar()
        cliente.config.parent.mkdir(parents=True, exist_ok=True)
        cliente.config.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")


def agora() -> datetime:
    return datetime.now(BRASILIA)


def registrar_post(cliente: Cliente, linha: str, detalhes: dict | None = None) -> None:
    """
    Guarda o post em dois formatos: uma linha de texto fácil de ler e um JSON
    com os detalhes (produto, imagem, número do post no WhatsApp) para o
    painel cruzar com as visualizações.
    """
    caminho = cliente.historico
    caminho.parent.mkdir(parents=True, exist_ok=True)
    momento = agora()
    with caminho.open("a", encoding="utf-8") as arquivo:
        arquivo.write(f"{momento:%d/%m/%Y %H:%M} | {linha}\n")
    registro = {"quando": momento.isoformat(timespec="seconds"), "linha": linha, **(detalhes or {})}
    with cliente.posts.open("a", encoding="utf-8") as arquivo:
        arquivo.write(json.dumps(registro, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------- #
# Lista de produtos e sorteio
# ---------------------------------------------------------------------- #

def ler_lista(cliente: Cliente, caminho: Path | None = None) -> list[tuple[str, Produto]]:
    """
    Lê a lista (mesmo formato do produtos.txt: link | preco_por | preco_de | cupom).

    Diferente do lote, uma linha mal escrita aqui PARA o robô: ninguém está
    olhando o terminal, então é melhor parar e avisar do que pular calado.
    """
    caminho = caminho or cliente.lista
    if not caminho.exists():
        raise RoboError(f"Lista de produtos não encontrada ({caminho}).")
    produtos = []
    for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), 1):
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        try:
            produtos.append((linha, com_etiqueta_de_afiliado(interpretar_linha(linha), cliente.etiqueta)))
        except ProdutoInvalidoError as erro:
            raise RoboError(f"Linha {numero} da lista está errada: {erro}") from erro
    if not produtos:
        raise RoboError("A lista de produtos está vazia.")
    return produtos


def com_etiqueta_de_afiliado(produto: Produto, etiqueta: str) -> Produto:
    """
    Com etiqueta de afiliado, TODO post do cliente usa a etiqueta dele, mesmo
    que o link da lista seja de outra pessoa (ex.: amzn.to de outro afiliado).
    Apagar o link da oferta faz o pipeline montar o link com a etiqueta a
    partir do código do produto (ASIN).
    """
    if not etiqueta:
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

    cliente: Cliente
    # Sem informar, fala com a ponte do WhatsApp do próprio cliente.
    whatsapp: WhatsAppService | None = None
    # Quem consulta o preço na Amazon (ASIN -> (atual, "De")). Nos testes
    # entra um dublê, para não acessar a internet.
    buscar_preco: BuscarPreco | None = None
    _pipeline: Pipeline | None = None

    def __post_init__(self) -> None:
        if self.whatsapp is None:
            self.whatsapp = WhatsAppService(self.cliente.url_whatsapp)

    @property
    def canal(self) -> str:
        return self.cliente.canal

    def _preco_do_momento(self, asin: str) -> tuple[float | None, float | None]:
        if self.buscar_preco is None:
            amazon = AmazonService()

            def buscar(codigo: str) -> tuple[float | None, float | None]:
                return amazon.extrair_preco(amazon.baixar_html(amazon.montar_url_limpa(codigo)))

            self.buscar_preco = buscar
        # A Amazon às vezes entrega a página sem o quadro de preço; uma
        # segunda tentativa costuma vir completa.
        for tentativa in range(2):
            try:
                atual, riscado = self.buscar_preco(asin)
            except AmazonError as erro:
                log.info("  Não consegui abrir a página para ver o preço: %s", erro)
                return None, None
            if atual is not None:
                return atual, riscado
            if tentativa == 0:
                time.sleep(3)
        return None, None

    def _texto_com_preco_do_momento(self, texto: str, produto: Produto, detalhes: dict) -> tuple[str, dict]:
        """
        Remonta o texto com o preço de agora. A chamada (headline), o nome e
        o link continuam os mesmos da peça; só a linha do preço muda. Se a
        Amazon não mostrar o preço, fica o preço da lista.
        """
        blocos = texto.split("\n\n")
        link = next((b[1:].strip() for b in blocos if b.startswith("🔗")), "")
        nome = next((b[1:].strip() for b in blocos if b.startswith("✅")), "")
        asin = detalhes.get("asin") or (re.search(r"/dp/([A-Z0-9]{10})", link) or [None, None])[1]
        if not (ATUALIZAR_PRECO and asin and link and nome):
            return texto, {"preco_fonte": "lista"}

        atual, riscado = self._preco_do_momento(asin)
        if atual is None:
            log.info("  A Amazon não mostrou o preço agora; vai o preço da lista.")
            return texto, {"preco_fonte": "lista"}

        oferta = produto.ofertas[0]
        de = riscado if riscado and riscado > atual else (oferta.preco_de if oferta.preco_de and oferta.preco_de > atual else None)
        novo = MessageService.formatar_mensagem(
            headline=blocos[0], produto=nome, link=link, preco_por=atual, preco_de=de, cupom=oferta.cupom,
        )
        if atual != oferta.preco_por:
            log.info("  Preço atualizado: R$ %.2f na lista -> R$ %.2f agora.", oferta.preco_por, atual)
        return novo, {"preco_fonte": "amazon", "preco": atual, "preco_de": de}

    def _gerar_ou_reaproveitar(self, linha: str, produto: Produto) -> tuple[Path, str, dict]:
        arquivo_cache = self.cliente.cache
        cache = json.loads(arquivo_cache.read_text(encoding="utf-8")) if arquivo_cache.exists() else {}
        chave = f"{linha} [tag={self.cliente.etiqueta}]"
        guardada = cache.get(chave)
        if guardada and Path(guardada["imagem"]).exists():
            log.info("  Reaproveitando a peça já gerada.")
            return Path(guardada["imagem"]), guardada["texto"], guardada.get("detalhes", {})

        if self._pipeline is None:  # carrega o modelo de recorte só quando precisa
            self._pipeline = Pipeline(
                pasta_saida=self.cliente.pecas,
                arroba_canal=self.cliente.arroba or None,
                etiqueta_afiliado=self.cliente.etiqueta or None,
            )
        with _GERANDO:
            peca = self._pipeline.processar(
                produto, ao_avancar=lambda n, etapa: log.info("  [%s %d/6] %s...", self.cliente.id, n, etapa)
            )
        texto = peca.mensagens["amazon"]
        detalhes = {"asin": peca.asin, "produto": peca.nome_produto}
        cache[chave] = {"imagem": str(peca.caminho_imagem), "texto": texto, "detalhes": detalhes}
        arquivo_cache.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        return peca.caminho_imagem, texto, detalhes

    def postar_um(self, estado: Estado) -> None:
        """Sorteia, gera e posta UM produto. Qualquer falha vira RoboError."""
        linha, produto = sortear(ler_lista(self.cliente), estado.ultimo)
        log.info("[%s] Sorteado: %s", self.cliente.id, linha)
        try:
            imagem, texto, detalhes = self._gerar_ou_reaproveitar(linha, produto)
        except ERROS_DO_PIPELINE as erro:
            raise RoboError(f"Falhou ao gerar a peça de:\n{linha}\nMotivo: {erro}") from erro
        texto, info_preco = self._texto_com_preco_do_momento(texto, produto, detalhes)
        try:
            resultado = self.whatsapp.postar(self.canal, imagem, texto)
        except WhatsAppError as erro:
            raise RoboError(f"Falhou ao postar no WhatsApp: {erro}") from erro
        if not resultado.get("etiquetaIA"):
            log.warning("  Post sem a etiqueta de IA (o WhatsApp não aceitou). Marque à mão.")

        registrar_post(self.cliente, linha, {
            **detalhes,
            **info_preco,
            "imagem": imagem.name,
            "server_id": resultado.get("serverId"),
            "etiqueta_ia": bool(resultado.get("etiquetaIA")),
        })
        # Relê o estado antes de gravar: alguém pode ter desligado o robô
        # enquanto a peça era gerada, e isso não pode ser desfeito aqui.
        estado = Estado.carregar(self.cliente)
        estado.ultimo, estado.ultimo_em = linha, f"{agora():%d/%m/%Y %H:%M}"
        estado.salvar(self.cliente)
        log.info("  ✓ [%s] Postado no canal.", self.cliente.id)

    def desligar_e_avisar(self, estado: Estado, motivo: str) -> None:
        """Para de postar e manda o motivo para o próprio número de teste."""
        log.error("[%s] ROBÔ DESLIGADO: %s", self.cliente.id, motivo)
        estado.ligado, estado.motivo = False, motivo
        estado.salvar(self.cliente)
        try:
            self.whatsapp.avisar(
                f"⚠️ Robô de achadinhos PAROU.\n\n{motivo}\n\nCorrija e ligue de novo."
            )
        except WhatsAppError as erro:
            log.error("Também não consegui avisar no WhatsApp: %s", erro)

    def rodar(self) -> None:
        """Laço principal: confere o estado de tempos em tempos e posta na hora certa."""
        log.info("[%s] Robô de pé, no canal %r. Intervalo e horário vêm do painel.", self.cliente.id, self.canal)
        ultimo_post: datetime | None = None
        while True:
            try:
                ultimo_post = self._conferir(ultimo_post)
            except Exception:  # um erro inesperado não pode derrubar os outros clientes
                log.exception("[%s] Erro inesperado; tento de novo na próxima conferida.", self.cliente.id)
            time.sleep(SEGUNDOS_ENTRE_CONFERIDAS)

    def _conferir(self, ultimo_post: datetime | None) -> datetime | None:
        """Uma conferida: posta se for a hora (ou se o painel pediu). Devolve o horário do último post."""
        cliente = self.cliente
        estado, config, momento = Estado.carregar(cliente), Config.carregar(cliente), agora()
        pedido_do_painel = cliente.postar_agora.exists()
        na_hora = ultimo_post is None or momento >= ultimo_post + timedelta(minutes=config.minutos_entre_posts)
        if pedido_do_painel or (estado.ligado and dentro_do_horario(momento, config) and na_hora):
            cliente.postar_agora.unlink(missing_ok=True)
            if not self.whatsapp.esta_pronto():
                log.info("[%s] Esperando o WhatsApp conectar...", cliente.id)
            else:
                try:
                    self.postar_um(estado)
                except RoboError as erro:
                    self.desligar_e_avisar(estado, str(erro))
                ultimo_post = agora()
        return ultimo_post


def rodar_todos() -> None:
    """Um robô (thread) por cliente; o processo fica de pé enquanto houver algum."""
    lista = clientes.carregar()
    if not lista:
        log.error("Nenhum cliente configurado (WHATSAPP_CANAL no .env ou clientes.json).")
        return
    linhas = [threading.Thread(target=Robo(cliente).rodar, name=cliente.id, daemon=True) for cliente in lista]
    for linha in linhas:
        linha.start()
    for linha in linhas:
        linha.join()


# ---------------------------------------------------------------------- #
# Linha de comando
# ---------------------------------------------------------------------- #

def _escolher_cliente(argumentos: list[str]) -> Cliente | None:
    """--cliente <id> escolhe o cliente; sem ele, o principal."""
    lista = clientes.carregar()
    if "--cliente" in argumentos:
        id_ = argumentos[argumentos.index("--cliente") + 1]
        return next((c for c in lista if c.id == id_), None)
    return next((c for c in lista if c.id == "principal"), lista[0] if lista else None)


def main(argumentos: list[str]) -> int:
    configurar_log()
    comando = argumentos[0] if argumentos else "status"
    if comando == "rodar":
        rodar_todos()
        return 0
    cliente = _escolher_cliente(argumentos)
    if cliente is None:
        print("Cliente não encontrado. Veja a lista com: python clientes.py listar")
        return 1
    estado = Estado.carregar(cliente)

    if comando == "ligar":
        estado.ligado, estado.motivo = True, ""
        estado.salvar(cliente)
        print(f"Robô LIGADO. O próximo post sai em até {Config.carregar(cliente).minutos_entre_posts} minuto(s).")
    elif comando == "desligar":
        estado.ligado, estado.motivo = False, ""
        estado.salvar(cliente)
        print("Robô DESLIGADO. Nada mais será postado até ligar de novo.")
    elif comando == "status":
        print("Ligado" if estado.ligado else f"Desligado {estado.motivo and '- ' + estado.motivo}")
        print(f"Último post: {estado.ultimo_em or 'nenhum'} {estado.ultimo}")
    elif comando == "agora":
        try:
            Robo(cliente).postar_um(estado)
        except RoboError as erro:
            print(f"Falhou: {erro}")
            return 2
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
