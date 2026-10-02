# Robô de Achadinhos

Transforma links de produtos da Amazon em **peças prontas para divulgar**:
uma imagem ambientada (com selo de avaliação) e o texto da oferta.

O robô **não publica** nada sozinho. Você revisa a peça, copia e posta no
canal (modo semiautomático).

---

## Usando pelo app (o jeito fácil)

1. Abra o link do app no celular ou no computador.
2. Cole o **link do produto na Amazon**. É de lá que vêm a foto e a avaliação.
3. Em **Onde postar**, preencha as lojas em que o produto está à venda
   (Amazon, Shopee, Mercado Livre, Magalu), cada uma com o **seu link de
   afiliado** e o **preço POR** (preço DE e cupom são opcionais).
   - Na Amazon o link pode ficar vazio se a `AMAZON_TAG` estiver no `.env`:
     o robô monta o link de afiliado sozinho.
   - Nas outras lojas, gere o link no app ou painel de afiliado da loja
     ("compartilhar" ou "gerar link") e cole no campo.
4. Toque em **Gerar peça** e acompanhe as etapas na tela (cerca de 15
   segundos). Enquanto isso, você já pode colar o próximo link.
5. Quando a peça ficar pronta, a imagem é a mesma para todas as lojas, e há
   um botão **Copiar post** para cada loja preenchida (cada texto sai com o
   link e o preço daquela loja). Use também **Copiar imagem**, **Baixar
   imagem** ou, no celular, **Compartilhar**.
6. Não gostou? **Gerar outra versão** faz uma nova, com outro cenário e
   outra frase.

O link do app é secreto: só entra quem tem o link. Não compartilhe.

---

## Instalação (só na primeira vez)

Você precisa do **Python 3.12 ou mais novo** ([python.org](https://www.python.org/downloads/),
marque a opção *"Add Python to PATH"* ao instalar).

Abra o **PowerShell** dentro da pasta do projeto e rode, um de cada vez:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> Se o PowerShell reclamar de "execução de scripts desabilitada", rode
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` e tente de novo.

Na **primeira execução** o robô baixa um modelo de IA (~170 MB) para recortar
o fundo das fotos. Isso acontece uma vez só.

## Configurar as chaves (`.env`)

1. Copie o arquivo `.env.exemplo` e renomeie a cópia para `.env`.
2. Abra o `.env` no Bloco de Notas e preencha:
   - `PHOTOROOM_SANDBOX_KEY`: chave do [PhotoRoom](https://www.photoroom.com/api)
     (a sandbox é grátis; a imagem sai com marca d'água "Photoroom").
   - `ANTHROPIC_API_KEY`: chave do [Claude](https://console.anthropic.com/).
     Custa uma fração de centavo por produto.
   - `APP_TOKEN`: o código secreto que vai no link do app. Use algo longo e
     aleatório (o próprio `.env.exemplo` mostra como gerar).
   - `CANAL_ARROBA`: o @ do seu canal, que vira a marca d'água da peça.
     É opcional; sem ele a imagem sai sem marca d'água.
   - `AMAZON_TAG`: sua etiqueta de afiliado da Amazon (ex.: `seunome-20`).
     Opcional; com ela o app monta o link de afiliado da Amazon sozinho.

Nunca compartilhe o `.env`: ele contém as suas chaves.

## Ligar o app no computador

```powershell
.\.venv\Scripts\Activate.ps1
python servidor.py
```

O terminal mostra o link (`http://localhost:8000/p/SEU_CODIGO/`). Deixe a
janela aberta enquanto usa o app.

## Colocar no ar numa VPS

O app precisa de uma VPS **Ubuntu 22.04 ou 24.04** com pelo menos **2 GB de
RAM** (4 GB deixa folga). No servidor, como root:

```bash
curl -fsSLO https://raw.githubusercontent.com/brucebatista01/sistema-achadinhos/main/deploy/instalar_vps.sh
bash instalar_vps.sh
```

Na primeira vez o script cria o `/opt/achadinhos/.env`. Preencha as chaves
(`nano /opt/achadinhos/.env`) e rode o script de novo. Ele instala o Docker,
liga o firewall, sobe o app com HTTPS automático e mostra o link.

Para atualizar depois de mudanças no GitHub, é só rodar o script de novo.

## Alternativa: lote pelo terminal

Para gerar vários produtos de uma vez sem abrir o app, preencha o
`produtos.txt` (um produto por linha) e rode `python main.py`:

```
link | preço por | preço de | cupom
https://amzn.to/abc123 | 129,90 | 199,90
https://amzn.to/xyz789 | 59,90 | 89,90 | CUPOM10
```

As peças ficam na pasta `output/` (`ASIN_final.png` e `ASIN_mensagem.txt`).

## Problemas comuns

| Mensagem | O que fazer |
|---|---|
| `Chave ... não encontrada (verifique o .env)` | Confira se o arquivo se chama `.env` (e não `.env.txt`) e se a chave está preenchida. |
| `Não foi possível encontrar o ASIN no link` | O link não é de um produto da Amazon. Copie o link direto da página do produto. |
| `Falha ao baixar HTML: 500` / `503` | Produto indisponível ou a Amazon bloqueou temporariamente. Tente mais tarde. |
| "Copiar imagem" não funciona | Alguns navegadores não deixam copiar imagens. Use "Baixar imagem" ou "Compartilhar". |

## Limitações conhecidas

- Só funciona com **Amazon** por enquanto. A Shopee bloqueia robôs.
- Com a chave sandbox do PhotoRoom, a imagem sai com marca d'água "Photoroom".
  Para publicar sem ela, é preciso a chave paga (US$ 0,10 por imagem); o
  robô ainda não usa essa chave, então fale com o Bruce antes de ativar.
- O histórico de peças do app fica na memória: se o servidor reiniciar, a
  lista na tela zera (as imagens continuam em `output/web/`).

## Para desenvolvedores

Estrutura:

```
pipeline.py        # núcleo: um produto -> uma peça (usado pelo app e pelo lote)
main.py            # modo lote pelo terminal
servidor.py        # liga o app web
Dockerfile, docker-compose.yml, deploy/   # publicação na VPS (app + Caddy com HTTPS)
web/app.py         # API (FastAPI): link secreto, fila de peças, progresso
web/static/        # interface (HTML, CSS e JavaScript puros)
services/          # um serviço por etapa (Amazon, imagem, visão, composição, mensagem)
tests/             # testes automatizados
```

Os testes não acessam a internet nem gastam crédito de API: as chamadas
externas são substituídas por dublês.

```powershell
pip install -r requirements-dev.txt
python -m pytest
```
