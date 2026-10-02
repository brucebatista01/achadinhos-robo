/*
 * Interface do Robô de Achadinhos.
 *
 * Fluxo: o formulário cria um "trabalho" no servidor (POST), e a página
 * consulta o andamento de todos os trabalhos ativos a cada segundo
 * (polling) até ficarem prontos. Cada peça é um cartão que se atualiza
 * no lugar, sem recarregar a página.
 *
 * A foto e a avaliação vêm sempre do link da Amazon. Cada loja preenchida
 * (Amazon, Shopee, Mercado Livre, Magalu) vira um post com o seu link e o
 * seu preço, todos com a mesma imagem.
 *
 * Segurança: todo texto vindo do usuário ou do servidor entra na tela via
 * textContent (nunca innerHTML), então um link colado não vira HTML.
 */

"use strict";

const ETAPAS = [
  "Buscando a foto oficial na Amazon",
  "Recortando o fundo",
  "Escolhendo o cenário com IA",
  "Montando a cena",
  "Aplicando o selo de avaliação",
  "Escrevendo a mensagem",
];
// Mesma ordem e mesmas chaves de LOJAS em pipeline.py.
const LOJAS = [
  { chave: "amazon", nome: "Amazon", dicaLink: "Opcional (usa sua etiqueta)" },
  { chave: "shopee", nome: "Shopee", dicaLink: "https://s.shopee.com.br/..." },
  { chave: "mercadolivre", nome: "Mercado Livre", dicaLink: "https://mercadolivre.com/sec/..." },
  { chave: "magalu", nome: "Magalu", dicaLink: "https://www.magazinevoce.com.br/..." },
];
const CAMPOS = ["link", "preco_por", "preco_de", "cupom"];
const INTERVALO_CONSULTA_MS = 1200;
const ROTULO_ESTADO = { fila: "Na fila", processando: "Gerando", pronto: "Pronta", erro: "Falhou" };

const formulario = document.getElementById("formulario");
const botaoGerar = document.getElementById("botao-gerar");
const botaoColar = document.getElementById("botao-colar");
const erroFormulario = document.getElementById("erro-formulario");
const listaPecas = document.getElementById("lista-pecas");
const vazio = document.getElementById("vazio");
const modeloPeca = document.getElementById("modelo-peca");
const modeloLoja = document.getElementById("modelo-loja");
const modeloPost = document.getElementById("modelo-post");
const containerLojas = document.getElementById("lojas");
const aviso = document.getElementById("aviso");

// id do trabalho -> { cartao, assinatura, dados }
const cartoes = new Map();
let consultaAgendada = null;

// ---------------------------------------------------------------------- //
// Utilitários
// ---------------------------------------------------------------------- //

function avisar(texto) {
  aviso.textContent = texto;
  aviso.classList.add("visivel");
  clearTimeout(avisar.timer);
  avisar.timer = setTimeout(() => aviso.classList.remove("visivel"), 2200);
}

function formatarPreco(valor) {
  return valor.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function resumirLink(link) {
  try {
    const url = new URL(link);
    return url.hostname.replace(/^www\./, "") + url.pathname.slice(0, 24);
  } catch {
    return link;
  }
}

function removerCartao(id) {
  cartoes.get(id)?.cartao.remove();
  cartoes.delete(id);
  vazio.hidden = cartoes.size > 0;
}

async function api(caminho, opcoes = {}) {
  const resposta = await fetch(caminho, {
    headers: { "Content-Type": "application/json" },
    ...opcoes,
  });
  // 204 (exclusão) não tem corpo: o catch devolve um objeto vazio.
  const dados = await resposta.json().catch(() => ({}));
  if (!resposta.ok) {
    throw new Error(typeof dados.detail === "string" ? dados.detail : "Não foi possível falar com o servidor.");
  }
  return dados;
}

async function copiarTexto(texto) {
  try {
    await navigator.clipboard.writeText(texto);
    return true;
  } catch {
    // Plano B para navegadores que bloqueiam a API moderna: o jeito antigo,
    // selecionando um campo de texto invisível e pedindo "copiar".
    const campo = document.createElement("textarea");
    campo.value = texto;
    campo.setAttribute("readonly", "");
    campo.style.cssText = "position:fixed;top:0;left:0;opacity:0";
    document.body.append(campo);
    campo.select();
    const ok = document.execCommand("copy");
    campo.remove();
    return ok;
  }
}

// ---------------------------------------------------------------------- //
// Blocos das lojas no formulário
// ---------------------------------------------------------------------- //

function criarBlocosDasLojas() {
  for (const loja of LOJAS) {
    const bloco = modeloLoja.content.firstElementChild.cloneNode(true);
    bloco.dataset.loja = loja.chave;
    bloco.querySelector(".loja-nome").textContent = loja.nome;
    // Cada campo ganha um id único para o <label for> funcionar.
    for (const campo of CAMPOS) {
      const id = `${loja.chave}-${campo}`;
      bloco.querySelector(`input[data-campo="${campo}"]`).id = id;
      bloco.querySelector(`label[data-campo="${campo}"]`).htmlFor = id;
    }
    bloco.querySelector('input[data-campo="link"]').placeholder = loja.dicaLink;
    // A Amazon já vem aberta: é a loja mais usada e a única com link opcional.
    bloco.open = loja.chave === "amazon";
    bloco.addEventListener("input", () => atualizarResumoDaLoja(bloco));
    containerLojas.append(bloco);
  }
}

function lerLoja(bloco) {
  const valores = {};
  for (const campo of CAMPOS) {
    valores[campo] = bloco.querySelector(`input[data-campo="${campo}"]`).value.trim();
  }
  return valores;
}

function atualizarResumoDaLoja(bloco) {
  // Mostra o preço no cabeçalho, para ver de relance quais lojas foram preenchidas.
  const { preco_por: preco } = lerLoja(bloco);
  bloco.querySelector(".loja-resumo").textContent = preco ? `R$ ${preco}` : "";
}

function limparLojas() {
  for (const bloco of containerLojas.children) {
    bloco.querySelectorAll("input").forEach((entrada) => { entrada.value = ""; });
    atualizarResumoDaLoja(bloco);
    bloco.open = bloco.dataset.loja === "amazon";
  }
}

// ---------------------------------------------------------------------- //
// Formulário
// ---------------------------------------------------------------------- //

function mostrarErroFormulario(texto, campo) {
  erroFormulario.textContent = texto;
  erroFormulario.hidden = false;
  if (campo) {
    campo.setAttribute("aria-invalid", "true");
    campo.closest("details")?.setAttribute("open", "");
    campo.focus();
  }
}

function limparErroFormulario() {
  erroFormulario.hidden = true;
  formulario.querySelectorAll("[aria-invalid]").forEach((c) => c.removeAttribute("aria-invalid"));
}

function acompanhar(trabalho) {
  atualizarCartao(trabalho);
  agendarConsulta();
  return trabalho;
}

formulario.addEventListener("submit", async (evento) => {
  evento.preventDefault();
  limparErroFormulario();

  const linkAmazon = formulario.link.value.trim();
  if (!linkAmazon) return mostrarErroFormulario("Cole o link do produto na Amazon.", formulario.link);

  // Só vão para o servidor as lojas com algum campo preenchido.
  const ofertas = {};
  for (const bloco of containerLojas.children) {
    const valores = lerLoja(bloco);
    if (Object.values(valores).some(Boolean)) ofertas[bloco.dataset.loja] = valores;
  }
  // Checagem rápida só para responder na hora; o servidor valida tudo de novo.
  if (Object.keys(ofertas).length === 0) {
    const precoAmazon = containerLojas.querySelector('input[data-campo="preco_por"]');
    return mostrarErroFormulario("Preencha o preço de pelo menos uma loja.", precoAmazon);
  }

  botaoGerar.disabled = true;
  botaoGerar.textContent = "Enviando...";
  try {
    acompanhar(await api("api/pecas", {
      method: "POST",
      body: JSON.stringify({ link_amazon: linkAmazon, ofertas }),
    }));
    formulario.reset();
    limparLojas();
    avisar("Peça na fila! Já pode colar o próximo link.");
    formulario.link.focus();
  } catch (erro) {
    mostrarErroFormulario(erro.message);
  } finally {
    botaoGerar.disabled = false;
    botaoGerar.textContent = "Gerar peça";
  }
});

// Botão "Colar": só aparece se o navegador permitir ler a área de transferência.
if (navigator.clipboard && navigator.clipboard.readText) {
  botaoColar.hidden = false;
  botaoColar.addEventListener("click", async () => {
    try {
      formulario.link.value = (await navigator.clipboard.readText()).trim();
      containerLojas.querySelector('input[data-campo="preco_por"]').focus();
    } catch {
      avisar("Não consegui colar. Use segurar e colar no campo.");
    }
  });
}

// ---------------------------------------------------------------------- //
// Cartões das peças
// ---------------------------------------------------------------------- //

function criarCartao(id) {
  const cartao = modeloPeca.content.firstElementChild.cloneNode(true);
  const listaEtapas = cartao.querySelector(".etapas");
  for (const nome of ETAPAS) {
    const item = document.createElement("li");
    item.textContent = nome;
    listaEtapas.append(item);
  }
  cartao.addEventListener("click", (evento) => {
    const botao = evento.target.closest("[data-acao]");
    if (botao) executarAcao(botao.dataset.acao, id, botao, evento);
  });
  return cartao;
}

function preencherPosts(cartao, mensagens) {
  const container = cartao.querySelector(".posts");
  container.replaceChildren();
  for (const { loja, nome_loja: nome, texto } of mensagens) {
    const post = modeloPost.content.firstElementChild.cloneNode(true);
    post.querySelector(".post-loja").textContent = nome;
    const botao = post.querySelector('[data-acao="copiar-post"]');
    botao.textContent = `Copiar post ${nome}`;
    botao.dataset.loja = loja;
    post.querySelector(".peca-mensagem").textContent = texto;
    container.append(post);
  }
}

function atualizarCartao(dados) {
  let registro = cartoes.get(dados.id);
  if (!registro) {
    registro = { cartao: criarCartao(dados.id), assinatura: "" };
    cartoes.set(dados.id, registro);
    // Peças novas entram no topo da lista.
    listaPecas.prepend(registro.cartao);
  }
  registro.dados = dados;

  // Só mexe no DOM quando algo mudou: evita a imagem "piscar" a cada consulta.
  const assinatura = `${dados.estado}:${dados.etapa}`;
  if (assinatura === registro.assinatura) return;
  registro.assinatura = assinatura;

  const { cartao } = registro;
  const { entrada } = dados;
  cartao.dataset.estado = dados.estado;
  cartao.querySelector(".selo-estado").textContent = ROTULO_ESTADO[dados.estado] || dados.estado;
  cartao.querySelector(".peca-titulo").textContent = dados.nome_produto || resumirLink(entrada.link_amazon);
  cartao.querySelector(".peca-detalhe").textContent = entrada.ofertas
    .map((o) => `${o.nome_loja} ${formatarPreco(o.preco_por)}`)
    .join(" · ");

  const emAndamento = dados.estado === "fila" || dados.estado === "processando";
  cartao.querySelector(".peca-progresso").hidden = !emAndamento;
  cartao.querySelector(".peca-pronta").hidden = dados.estado !== "pronto";
  cartao.querySelector(".peca-erro").hidden = dados.estado !== "erro";

  if (emAndamento) {
    // A etapa atual conta como meio caminho, para a barra andar já no início.
    const progresso = Math.max(0, dados.etapa - 0.5) / dados.total_etapas;
    cartao.querySelector(".barra-preenchida").style.width = `${Math.round(progresso * 100)}%`;
    cartao.querySelectorAll(".etapas li").forEach((item, indice) => {
      item.classList.toggle("feita", indice + 1 < dados.etapa);
      item.classList.toggle("atual", indice + 1 === dados.etapa);
    });
  }

  if (dados.estado === "pronto") {
    cartao.querySelector(".peca-imagem").src = dados.url_imagem;
    cartao.querySelector(".sem-selo").hidden = !!dados.avaliacao;
    preencherPosts(cartao, dados.mensagens);
    const baixar = cartao.querySelector('[data-acao="baixar"]');
    baixar.href = dados.url_imagem;
    baixar.download = `achadinho-${dados.id}.png`;
    // "Compartilhar" só faz sentido onde o navegador sabe enviar arquivos (celular).
    const podeCompartilhar = !!(navigator.canShare && navigator.canShare({
      files: [new File([""], "teste.png", { type: "image/png" })],
    }));
    cartao.querySelector('[data-acao="compartilhar"]').hidden = !podeCompartilhar;
  }

  if (dados.estado === "erro") {
    cartao.querySelector(".peca-erro-texto").textContent = dados.erro;
  }

  vazio.hidden = cartoes.size > 0;
}

// ---------------------------------------------------------------------- //
// Ações dos botões
// ---------------------------------------------------------------------- //

async function baixarImagem(dados) {
  const resposta = await fetch(dados.url_imagem);
  return resposta.blob();
}

async function executarAcao(acao, id, botao, evento) {
  const { dados } = cartoes.get(id);

  if (acao === "copiar-post") {
    // O botão fica dentro do <summary>: sem isso, o clique também abriria o post.
    evento.preventDefault();
    const post = dados.mensagens.find((m) => m.loja === botao.dataset.loja);
    avisar(await copiarTexto(post.texto)
      ? `Post ${post.nome_loja} copiado! Agora é só colar no canal.`
      : "Não consegui copiar. Abra o post e copie o texto manualmente.");
  }

  if (acao === "copiar-imagem") {
    try {
      // Passar a Promise (e não o blob pronto) é o que o Safari exige.
      await navigator.clipboard.write([
        new ClipboardItem({ "image/png": baixarImagem(dados) }),
      ]);
      avisar("Imagem copiada!");
    } catch {
      avisar("Este navegador não copia imagens. Use \"Baixar imagem\".");
    }
  }

  if (acao === "compartilhar") {
    try {
      const arquivo = new File([await baixarImagem(dados)], `achadinho-${dados.id}.png`, { type: "image/png" });
      // Compartilha só a imagem: o texto de cada loja é copiado pelo botão dela.
      await navigator.share({ files: [arquivo] });
    } catch (erro) {
      if (erro.name !== "AbortError") avisar("Não foi possível compartilhar.");
    }
  }

  if (acao === "excluir") {
    if (!window.confirm("Excluir esta peça? A imagem e os textos serão apagados do servidor.")) return;
    botao.disabled = true;
    try {
      await api(`api/pecas/${id}`, { method: "DELETE" });
      removerCartao(id);
      avisar("Peça excluída.");
    } catch (erro) {
      avisar(erro.message);
      botao.disabled = false;
    }
  }

  if (acao === "refazer") {
    botao.disabled = true;
    try {
      // O servidor já guarda os dados da peça: não precisa enviar de novo.
      acompanhar(await api(`api/pecas/${id}/refazer`, { method: "POST" }));
      avisar("Gerando outra versão...");
      window.scrollTo({ top: listaPecas.offsetTop - 16, behavior: "smooth" });
    } catch (erro) {
      avisar(erro.message);
    } finally {
      botao.disabled = false;
    }
  }
}

// ---------------------------------------------------------------------- //
// Consulta periódica do andamento
// ---------------------------------------------------------------------- //

function haTrabalhoAtivo() {
  for (const { dados } of cartoes.values()) {
    if (dados && (dados.estado === "fila" || dados.estado === "processando")) return true;
  }
  return false;
}

function agendarConsulta() {
  if (consultaAgendada) return;
  consultaAgendada = setTimeout(consultar, INTERVALO_CONSULTA_MS);
}

async function consultar() {
  consultaAgendada = null;
  try {
    const trabalhos = await api("api/pecas");
    // A lista vem da mais nova para a mais antiga; inserimos de trás para
    // frente para que "prepend" mantenha a mais nova no topo.
    for (const dados of trabalhos.slice().reverse()) atualizarCartao(dados);
    // Peças que sumiram do servidor (excluídas em outra aba ou pelo limite
    // do histórico) saem da tela também.
    const noServidor = new Set(trabalhos.map((t) => t.id));
    for (const id of [...cartoes.keys()]) if (!noServidor.has(id)) removerCartao(id);
  } catch {
    // Falha de rede passageira: tenta de novo na próxima rodada.
  }
  if (haTrabalhoAtivo()) agendarConsulta();
}

criarBlocosDasLojas();
consultar();
