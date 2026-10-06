/*
 * Ajuste pequeno no whatsapp-web.js, aplicado depois do `npm ci`.
 *
 * O WhatsApp Web removeu `msg.avParams()` (set/2026). A biblioteca ainda chama
 * essa função ao postar imagem em canal, e o post falha com
 * "msg.avParams is not a function". O próprio WhatsApp Web hoje manda só o
 * `mediaHandle`, sem `mediaMetadata`; então só chamamos avParams se existir.
 *
 * Quando a biblioteca corrigir isso, este script deixa de achar o trecho e
 * avisa, sem quebrar nada.
 */

const fs = require('fs');
const path = require('path');

const arquivo = path.join(__dirname, 'node_modules', 'whatsapp-web.js', 'src', 'util', 'Injected', 'Utils.js');
const antes = 'mediaMetadata: msg.avParams(),';
const depois = "...(typeof msg.avParams === 'function' ? { mediaMetadata: msg.avParams() } : {}),";

const codigo = fs.readFileSync(arquivo, 'utf8');
if (codigo.includes(antes)) {
    fs.writeFileSync(arquivo, codigo.replace(antes, depois));
    console.log('whatsapp-web.js ajustado (avParams opcional).');
} else {
    console.log('Ajuste do avParams não necessário (trecho não encontrado).');
}
