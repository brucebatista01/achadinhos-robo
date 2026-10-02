# Robô de Achadinhos

Transforma links de produtos da Amazon em **peças prontas para divulgar**:
uma imagem ambientada (com selo de avaliação) e o texto da oferta.

```
link da Amazon  ->  output/ASIN_final.png     (imagem)
                    output/ASIN_mensagem.txt  (texto para copiar e colar)
```

O robô **não publica** nada sozinho. Você revisa as peças em `output/` e
publica no canal do jeito que já faz hoje.

---

## 1. Instalação (só na primeira vez)

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

Na **primeira execução** o robô baixa um modelo de IA de ~1 GB para recortar
o fundo das fotos. Isso acontece uma vez só.

## 2. Configurar as chaves (`.env`)

1. Copie o arquivo `.env.exemplo` e renomeie a cópia para `.env`.
2. Abra o `.env` no Bloco de Notas e preencha:
   - `PHOTOROOM_SANDBOX_KEY`: chave do [PhotoRoom](https://www.photoroom.com/api)
     (a sandbox é grátis; a imagem sai com marca d'água "Photoroom").
   - `ANTHROPIC_API_KEY`: chave do [Claude](https://console.anthropic.com/).
     Custa uma fração de centavo por produto.
   - `CANAL_ARROBA`: o @ do seu canal, que vira a marca d'água da peça.
     É opcional; sem ele a imagem sai sem marca d'água.

Nunca compartilhe o `.env`: ele contém as suas chaves.

## 3. Colar os produtos (`produtos.txt`)

Um produto por linha, separando os campos com `|`:

```
link | preço por | preço de | cupom
```

Só o **link** e o **preço por** são obrigatórios. Exemplos:

```
https://amzn.to/abc123 | 129,90 | 199,90
https://amzn.to/xyz789 | 59,90 | 89,90 | CUPOM10
https://www.amazon.com.br/dp/B0CNHBV6W5 | 129,90
```

- Pode usar o seu link curto de afiliado (`amzn.to`). É esse link que vai
  na mensagem.
- Linhas em branco e linhas começando com `#` são ignoradas.
- Se o "preço de" for menor que o "preço por", ele é omitido da mensagem.

## 4. Rodar

Com o PowerShell aberto na pasta do projeto:

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

O terminal mostra o progresso de cada produto, e no final um resumo. Se um
produto der erro, o robô **avisa e segue para o próximo**.

Cada produto leva em torno de 1 minuto.

## 5. Revisar antes de publicar

Abra a pasta `output/` e confira cada peça:

- `ASIN_final.png`: a imagem pronta.
- `ASIN_mensagem.txt`: o texto. Abra, copie e cole no canal.
- `ASIN_sem_fundo.png`: o recorte do produto (só para conferência).

A imagem e a headline são geradas por IA, então **sempre dê uma olhada**
antes de publicar. Se não gostar de uma peça, rode de novo só aquele
produto: a IA gera um resultado diferente a cada vez.

## Problemas comuns

| Mensagem | O que fazer |
|---|---|
| `Chave ... não encontrada (verifique o .env)` | Confira se o arquivo se chama `.env` (e não `.env.txt`) e se a chave está preenchida. |
| `Não foi possível encontrar o ASIN no link` | O link não é de um produto da Amazon. Copie o link direto da página do produto. |
| `Falha ao baixar HTML: 500` / `503` | Produto indisponível ou a Amazon bloqueou temporariamente. Tente mais tarde. |
| `Linha N ignorada: Faltou o preço` | Falta o preço depois do link nessa linha do `produtos.txt`. |

## Limitações conhecidas

- Só funciona com **Amazon** por enquanto. A Shopee bloqueia robôs.
- Com a chave sandbox do PhotoRoom, a imagem sai com marca d'água "Photoroom".
  Para publicar sem ela, é preciso a chave paga (US$ 0,10 por imagem); o
  robô ainda não usa essa chave, então fale com o Bruce antes de ativar.

## Para desenvolvedores

Os testes automatizados não acessam a internet nem gastam crédito de API:
as chamadas externas são substituídas por dublês.

```powershell
pip install -r requirements-dev.txt
python -m pytest
```
