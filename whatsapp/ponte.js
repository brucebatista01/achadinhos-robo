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

const rotas = {
    'GET /status': async () => ({ pronto, numero: pronto ? cliente.info.wid.user : null }),

    'GET /canais': async () =>
        (await cliente.getChannels()).map((c) => ({ id: c.id._serialized, nome: c.name })),

    'POST /postar': async ({ canal, imagem, texto }) => {
        const destino = await acharCanal(canal);
        const midia = MessageMedia.fromFilePath(imagem);
        const mensagem = await destino.sendMessage(midia, { caption: texto });
        return { ok: true, id: mensagem && mensagem.id ? mensagem.id._serialized : null };
    },

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
