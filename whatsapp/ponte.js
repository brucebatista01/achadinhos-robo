/*
 * Ponte do WhatsApp: mantém o número de teste conectado e posta no canal.
 *
 * Por que existe: o WhatsApp NÃO tem API oficial para postar em canal. O
 * caminho que sobra é usar o próprio WhatsApp Web, como um navegador faria
 * (biblioteca whatsapp-web.js). É o jeito que mais se parece com uma pessoa
 * usando o WhatsApp no computador, e por isso o menos arriscado entre os não
 * oficiais. Mesmo assim é NÃO OFICIAL: o número pode ser banido. Use só com o
 * número de teste.
 *
 * O robô em Python (robo.py) não fala com o WhatsApp diretamente: ele chama
 * esta ponte por HTTP, só dentro da rede interna do Docker. Assim cada lado
 * usa a ferramenta que faz melhor (Python gera a peça, Node fala com o
 * WhatsApp Web), e a ponte fica conectada o tempo todo, sem relogar a cada post.
 *
 * Rotas:
 *   GET  /status         -> {pronto, numero}
 *   GET  /canais         -> canais que este número administra
 *   POST /postar         {canal, imagem, texto} -> posta imagem com legenda
 *   POST /avisar         {texto} -> manda mensagem para o próprio número
 */

const fs = require('fs');
const http = require('http');
const path = require('path');
const QRCode = require('qrcode');
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');

const PASTA_DADOS = process.env.PASTA_DADOS || path.join(__dirname, '..', 'dados');
const PORTA = Number(process.env.PORTA_PONTE || 3000);
const ARQUIVO_QR = path.join(PASTA_DADOS, 'qr.png');

let pronto = false;

const cliente = new Client({
    // A sessão fica salva em disco: depois do primeiro pareamento, o número
    // reconecta sozinho, sem precisar do celular por perto.
    authStrategy: new LocalAuth({ dataPath: path.join(PASTA_DADOS, 'sessao') }),
    puppeteer: {
        headless: true,
        // No Docker usamos o Chromium do sistema (ver Dockerfile.whatsapp).
        executablePath: process.env.CHROMIUM_PATH || undefined,
        args: ['--no-sandbox', '--disable-dev-shm-usage'],
    },
});

cliente.on('qr', async (qr) => {
    // Pareamento: o QR vira uma imagem para ser escaneada pelo celular em
    // "Aparelhos conectados". Também sai em texto no log, por garantia.
    await QRCode.toFile(ARQUIVO_QR, qr, { width: 400 });
    console.log(`QR novo salvo em ${ARQUIVO_QR}. Escaneie em WhatsApp > Aparelhos conectados.`);
    console.log(await QRCode.toString(qr, { type: 'terminal', small: true }));
});

cliente.on('ready', () => {
    pronto = true;
    fs.rmSync(ARQUIVO_QR, { force: true });
    console.log(`WhatsApp conectado como ${cliente.info.wid.user}.`);
});

cliente.on('disconnected', (motivo) => {
    // Sem conexão a ponte não serve para nada. Sair deixa o Docker
    // reiniciá-la (restart: unless-stopped), o que tenta reconectar.
    console.error(`WhatsApp desconectou: ${motivo}. Reiniciando a ponte.`);
    process.exit(1);
});

async function acharCanal(nomeOuId) {
    const canais = await cliente.getChannels();
    const canal = canais.find((c) => c.id._serialized === nomeOuId || c.name === nomeOuId);
    if (!canal) {
        const nomes = canais.map((c) => c.name).join(', ') || 'nenhum';
        throw new Error(`Canal "${nomeOuId}" não encontrado. Canais deste número: ${nomes}`);
    }
    return canal;
}

async function etiquetarComoIA(canalId, mensagemId) {
    /*
     * O mesmo que fazer à mão: segurar o post > ⋮ > "Adicionar etiqueta de
     * conteúdo de IA" > "Adicionar etiqueta". O WhatsApp pede essa etiqueta
     * em post feito com IA, e as nossas imagens e chamadas são.
     *
     * A biblioteca não tem isso pronto, então chamamos a mesma ação interna
     * que o botão do WhatsApp Web chama. Ela precisa do número que o servidor
     * deu ao post (serverId), que só chega um pouco depois do envio.
     */
    return cliente.pupPage.evaluate(async (canalId, mensagemId) => {
        const msg = window.require('WAWebCollections').Msg.get(mensagemId);
        for (let tentativa = 0; msg && msg.serverId == null && tentativa < 20; tentativa++) {
            await new Promise((r) => setTimeout(r, 500));
        }
        if (!msg || msg.serverId == null) return { etiqueta: false, serverId: null };
        const acao = window.require('WAWebNewsletterAddAiContentLabelAction');
        const ok = await acao.WAWebNewsletterAddAiContentLabelAction(canalId, String(msg.serverId), 'MESSAGE');
        return { etiqueta: ok === true, serverId: msg.serverId };
    }, canalId, mensagemId);
}

async function metricasDoCanal(canal, limite) {
    /*
     * Números para o painel: seguidores do canal e, de cada post recente,
     * visualizações, reações e encaminhamentos.
     *
     * Os números que ficam guardados no WhatsApp Web só se renovam quando
     * alguém abre o canal na tela, e a ponte não tem tela. Por isso pedimos
     * os números direto ao servidor do WhatsApp, com a mesma consulta que o
     * WhatsApp Web usa ("message_updates"), toda vez que o painel atualiza.
     */
    await canal.fetchMessages({ limit: limite, fromMe: true });
    return cliente.pupPage.evaluate(async (jid, limite) => {
        const chat = await window.WWebJS.getChat(jid, { getAsModel: false });
        const msgs = chat.msgs.getModelsArray()
            .filter((m) => m.serverId != null && m.serverId < 1e9 && m.type !== 'newsletter_notification')
            .slice(-limite);

        // Soma todos os "count" dentro de um pedaço da resposta do servidor
        // (o formato das reações muda de tempos em tempos; somar é robusto).
        const somarContagens = (valor) => {
            if (!valor || typeof valor !== 'object') return 0;
            let total = 0;
            for (const [chave, item] of Object.entries(valor)) {
                if (chave === 'count' && Number.isFinite(Number(item))) total += Number(item);
                else total += somarContagens(item);
            }
            return total;
        };

        const doServidor = {};
        let erroServidor = null;
        if (msgs.length) {
            const inicio = Math.min(...msgs.map((m) => m.serverId));
            try {
                const resposta = await Promise.race([
                    new Promise((_, rejeitar) => setTimeout(() => rejeitar(new Error('demorou demais')), 20000)),
                    window.require('WASmaxNewslettersGetNewsletterMessageUpdatesRPC').sendGetNewsletterMessageUpdatesRPC({
                        iqTo: jid,
                        messageUpdatesCount: Math.min(100, msgs.length + 5),
                        messageUpdatesBeforeOrAfterMixinMixinGroupArgs: {
                            messageUpdatesAfterMixin: { messageUpdatesAfter: inicio - 1 },
                        },
                    }),
                ]);
                const lista = resposta.value?.messageUpdatesMessagesNewsletterMessageResponsePayloadMixin?.message || [];
                for (const item of lista) {
                    doServidor[item.serverId] = {
                        visualizacoes: somarContagens(item.newsletterViewsCountViewsOrDeprecatedMixinGroup),
                        reacoes: somarContagens(item.newsletterReactionsMixin),
                        encaminhamentos: somarContagens(item.newsletterForwardsCountMixin),
                    };
                }
            } catch (e) {
                erroServidor = String((e && e.message) || e);
            }
        }

        let seguidores = null;
        try {
            const meta = await window.require('WAWebNewsletterMetadataQueryJob')
                .queryNewsletterMetadataByJid(jid, 'ADMIN', { subscribers: true });
            seguidores = meta.newsletterSubscribersMetadataMixin.subscribersCount;
        } catch (e) { /* segue sem o número de seguidores */ }

        const posts = msgs.map((m) => ({
            serverId: m.serverId,
            quando: m.t,
            legenda: (m.caption || m.body || '').slice(0, 300),
            // Sem resposta do servidor, ficam os números guardados (podem estar velhos).
            ...(doServidor[m.serverId] || {
                visualizacoes: m.viewCount || 0,
                reacoes: m.hasReaction ? 1 : 0,
                encaminhamentos: m.forwardsCount || 0,
            }),
            atualizado: Boolean(doServidor[m.serverId]),
        }));
        return { seguidores, posts, erroServidor };
    }, canal.id._serialized, limite);
}

const rotas = {
    'GET /status': async () => ({ pronto, numero: pronto ? cliente.info.wid.user : null }),

    'GET /canais': async () =>
        (await cliente.getChannels()).map((c) => ({ id: c.id._serialized, nome: c.name })),

    'POST /postar': async ({ canal, imagem, texto, etiquetaIA = true }) => {
        const destino = await acharCanal(canal);
        const midia = MessageMedia.fromFilePath(imagem);
        const mensagem = await destino.sendMessage(midia, { caption: texto });
        const id = mensagem && mensagem.id ? mensagem.id._serialized : null;
        const rotulo = etiquetaIA && id
            ? await etiquetarComoIA(destino.id._serialized, id)
            : { etiqueta: false, serverId: null };
        return { ok: true, id, etiquetaIA: rotulo.etiqueta, serverId: rotulo.serverId };
    },

    'POST /metricas': async ({ canal, limite = 60 }) => metricasDoCanal(await acharCanal(canal), limite),

    'POST /avisar': async ({ texto }) => {
        // "Conversar comigo mesmo": o aviso chega no próprio número de teste.
        await cliente.sendMessage(cliente.info.wid._serialized, texto);
        return { ok: true };
    },
};

function lerCorpo(req) {
    return new Promise((resolve, reject) => {
        let dados = '';
        req.on('data', (parte) => { dados += parte; });
        req.on('end', () => {
            try { resolve(dados ? JSON.parse(dados) : {}); } catch (erro) { reject(erro); }
        });
    });
}

http.createServer(async (req, res) => {
    const rota = rotas[`${req.method} ${req.url}`];
    let status = 200;
    let resposta;
    if (!rota) {
        status = 404;
        resposta = { erro: 'rota não existe' };
    } else if (!pronto && req.url !== '/status') {
        status = 503;
        resposta = { erro: 'WhatsApp ainda não conectado (falta parear ou está iniciando)' };
    } else {
        try {
            resposta = await rota(await lerCorpo(req));
        } catch (erro) {
            status = 500;
            resposta = { erro: erro.message };
        }
    }
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(resposta));
}).listen(PORTA, () => console.log(`Ponte ouvindo na porta ${PORTA}.`));

fs.mkdirSync(PASTA_DADOS, { recursive: true });
// O Chromium deixa uma "trava" na pasta da sessão com o nome do container.
// Quando o Docker recria o container (nome novo), a trava velha impede o
// Chromium de abrir. Só esta ponte usa a sessão, então é seguro apagar.
for (const trava of ['SingletonLock', 'SingletonSocket', 'SingletonCookie']) {
    fs.rmSync(path.join(PASTA_DADOS, 'sessao', 'session', trava), { force: true });
}
cliente.initialize();
