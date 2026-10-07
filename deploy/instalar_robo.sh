#!/usr/bin/env bash
# Instala ou atualiza o ROBÔ AUTOMÁTICO na VPS, separado do app web.
# O app web do Danilo (/opt/achadinhos) não é tocado.
#
# Uso, como root:  bash instalar_robo.sh
# Pode rodar de novo para atualizar: mantém .env, lista, sessão do WhatsApp.

set -euo pipefail

PASTA="/opt/achadinhos-robo"
# As pontes de WhatsApp dos clientes extras ficam num segundo arquivo,
# escrito pelo cadastro de clientes (clientes.py) dentro de dados/.
[ -f "$PASTA/dados/docker-compose.clientes.yml" ] || echo "services: {}" > "$PASTA/dados/docker-compose.clientes.yml"
COMPOSE="docker compose -f $PASTA/docker-compose.robo.yml -f $PASTA/dados/docker-compose.clientes.yml"

# O código chega pelo publicar.ps1 (do PC, pasta E:\Danilo2), sem passar
# pelo repositório do projeto antigo.
if [ ! -f "$PASTA/robo.py" ]; then
  echo "Código não encontrado em $PASTA. Rode o publicar.ps1 no PC." >&2
  exit 1
fi
cd "$PASTA"

# Pasta de dados dividida pelos dois containers (usuário id 1000 nos dois).
mkdir -p dados/output
if [ ! -f dados/estado.json ]; then
  # Começa DESLIGADO: só liga depois de parear o WhatsApp e conferir a lista.
  echo '{"ligado": false, "motivo": "recém-instalado", "ultimo": "", "ultimo_em": ""}' > dados/estado.json
fi
[ -f dados/lista.txt ] || cp produtos.txt dados/lista.txt
chown -R 1000:1000 dados

if [ ! -f .env ]; then
  # Reaproveita as chaves do app web (Anthropic, PhotoRoom, @ do canal).
  cp /opt/achadinhos/.env .env
  echo "WHATSAPP_CANAL=coloque_o_nome_do_canal" >> .env
  chmod 600 .env
fi

# Código secreto do link do painel. Trocar este valor derruba o link antigo.
if ! grep -q '^PAINEL_TOKEN=' .env; then
  echo "PAINEL_TOKEN=$(openssl rand -hex 24)" >> .env
fi

# Endereço do painel, para o cadastro de clientes mostrar o link pronto.
grep -q '^DOMINIO_PAINEL=' .env || echo "DOMINIO_PAINEL=robo.$(grep -E '^DOMINIO=' /opt/achadinhos/.env | cut -d= -f2)" >> .env

# Atalho "robo" no terminal da VPS:
#   robo status | ligar | desligar | log | historico | canais | reiniciar
#   robo clientes                                  lista os clientes
#   robo cliente-novo <id> "<canal>" <tag> <pin>    cadastra e sobe a ponte dele
#   robo cliente-remover <id>                       tira do robô (a pasta fica)
#   (ligar/desligar/status/agora aceitam --cliente <id>)
cat > /usr/local/bin/robo <<SCRIPT
#!/usr/bin/env bash
case "\${1:-status}" in
  log) $COMPOSE logs --tail 50 -f robo whatsapp ;;
  historico) tail -n 30 $PASTA/dados/postados.log ;;
  canais) $COMPOSE exec -T robo python -m services.whatsapp_service ;;
  reiniciar) $COMPOSE restart ;;
  clientes) $COMPOSE exec -T robo python clientes.py listar ;;
  cliente-novo)
    $COMPOSE exec -T robo python clientes.py novo "\$2" --canal "\$3" --tag "\$4" --pin "\$5" &&
    $COMPOSE up -d --build && $COMPOSE restart robo painel ;;
  cliente-remover)
    $COMPOSE exec -T robo python clientes.py remover "\$2" &&
    $COMPOSE up -d --remove-orphans && $COMPOSE restart robo painel ;;
  *) $COMPOSE exec -T robo python robo.py "\$@" ;;
esac
SCRIPT
chmod +x /usr/local/bin/robo

echo "==> Construindo e ligando (a primeira vez demora alguns minutos)"
$COMPOSE up -d --build

echo
echo "Pronto. O robô está de pé, mas DESLIGADO até você rodar: robo ligar"
