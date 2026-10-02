/*
 * Interface do Robô de Achadinhos.
 *
 * Fluxo: o formulário cria um "trabalho" no servidor (POST), e a página
 * consulta o andamento de todos os trabalhos ativos a cada segundo
 * (polling) até ficarem prontos. Cada peça é um cartão que se atualiza
 * no lugar, sem recarregar a página.
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
const INTERVALO_CONSULTA_MS = 1200;
const ROTULO_ESTADO = { fila: "Na fila", processando: "Gerando", pronto: "Pronta", erro: "Falhou" };

const formulario = document.getElementById("formulario");
const botaoGerar = document.getElementById("botao-gerar");
const botaoColar = document.getElementById("botao-colar");
const erroFormulario = document.getElementById("erro-formulario");
const listaPecas = document.getElementById("lista-pecas");
const vazio = document.getElementById("vazio");
const modeloPeca = document.getElementById("modelo-peca");
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

async function api(caminho, opcoes = {}) {
  const resposta = await fetch(caminho, {
    headers: { "Content-Type": "application/json" },
    ...opcoes,
  });
  const dados = await resposta.json().catch(() => ({}));
  if (!resposta.ok) {
    throw new Error(typeof dados.detail === "string" ? dados.detail : "Não foi possível falar com o servidor.");
  }
  return dados;
}

// ---------------------------------------------------------------------- //
// Formulário
// ---------------------------------------------------------------------- //

function mostrarErroFormulario(texto, campo) {
  erroFormulario.textContent = texto;
  erroFormulario.hidden = false;
  if (campo) {
    campo.setAttribute("aria-invalid", "true");
    campo.focus();
  }
}

function limparErroFormulario() {
  erroFormulario.hidden = true;
  formulario.querySelectorAll("[aria-invalid]").forEach((c) => c.removeAttribute("aria-invalid"));
}

async function enviarPedido(pedido) {
  const trabalho = await api("api/pecas", { method: "POST", body: JSON.stringify(pedido) });
  atualizarCartao(trabalho);
  agendarConsulta();
  return trabalho;
}

formulario.addEventListener("submit", async (evento) => {
  evento.preventDefault();
  limparErroFormulario();

  const pedido = {
    link: formulario.link.value.trim(),
    preco_por: formulario.preco_por.value.trim(),
    preco_de: formulario.preco_de.value.trim(),
    cupom: formulario.cupom.value.trim(),
  };
  // Checagens rápidas aqui só para responder na hora; o servidor valida de novo.
  if (!pedido.link) return mostrarErroFormulario("Cole o link do produto.", formulario.link);
  if (!pedido.preco_por) return mostrarErroFormulario("Informe o preço POR.", formulario.preco_por);

  botaoGerar.disabled = true;
  botaoGerar.textContent = "Enviando...";
  try {
    await enviarPedido(pedido);
    formulario.reset();
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
      formulario.preco_por.focus();
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
    if (botao) executarAcao(botao.dataset.acao, id, evento);
  });
  return cartao;
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
  cartao.querySelector(".peca-titulo").textContent = dados.nome_produto || resumirLink(entrada.link);

  const detalhes = [`Por ${formatarPreco(entrada.preco_por)}`];
  if (entrada.preco_de) detalhes.push(`de ${formatarPreco(entrada.preco_de)}`);
  if (entrada.cupom) detalhes.push(`cupom ${entrada.cupom}`);
  cartao.querySelector(".peca-detalhe").textContent = detalhes.join(" · ");

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
    cartao.querySelector(".peca-mensagem").textContent = dados.mensagem;
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

async function baixarImagem(dados) {
  const resposta = await fetch(dados.url_imagem);
  return resposta.blob();
}

async function executarAcao(acao, id, evento) {
  const { dados } = cartoes.get(id);

  if (acao === "copiar-texto") {
    avisar(await copiarTexto(dados.mensagem)
      ? "Texto copiado! Agora é só colar no canal."
      : "Não consegui copiar. Selecione o texto e copie manualmente.");
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
      await navigator.share({ files: [arquivo], text: dados.mensagem });
    } catch (erro) {
      if (erro.name !== "AbortError") avisar("Não foi possível compartilhar.");
    }
  }

  if (acao === "refazer") {
    evento.target.disabled = true;
    try {
      const { entrada } = dados;
      await enviarPedido({
        link: entrada.link,
        preco_por: String(entrada.preco_por),
        preco_de: entrada.preco_de ? String(entrada.preco_de) : "",
        cupom: entrada.cupom || "",
      });
      avisar("Gerando outra versão...");
      window.scrollTo({ top: listaPecas.offsetTop - 16, behavior: "smooth" });
    } catch (erro) {
      avisar(erro.message);
    } finally {
      evento.target.disabled = false;
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
  } catch {
    // Falha de rede passageira: tenta de novo na próxima rodada.
  }
  if (haTrabalhoAtivo()) agendarConsulta();
}

consultar();
