#!/usr/bin/env bash
# Instala ou atualiza o ROBÔ AUTOMÁTICO na VPS, separado do app web.
# O app web do Danilo (/opt/achadinhos) não é tocado.
#
# Uso, como root:  bash instalar_robo.sh
# Pode rodar de novo para atualizar: mantém .env, lista, sessão do WhatsApp.

set -euo pipefail

REPOSITORIO="https://github.com/brucebatista01/sistema-achadinhos.git"
RAMO="claude/project-thread-cnqooq"
PASTA="/opt/achadinhos-robo"
COMPOSE="docker compose -f $PASTA/docker-compose.robo.yml"

echo "==> Baixando o código"
if [ -d "$PASTA/.git" ]; then
  git -C "$PASTA" fetch -q origin "$RAMO"
  git -C "$PASTA" checkout -q "$RAMO"
  git -C "$PASTA" reset -q --hard "origin/$RAMO"
else
  git clone -q -b "$RAMO" "$REPOSITORIO" "$PASTA"
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

# Atalho "robo" no terminal da VPS:  robo status | ligar | desligar | log | qr
cat > /usr/local/bin/robo <<SCRIPT
#!/usr/bin/env bash
case "\${1:-status}" in
  log) $COMPOSE logs --tail 50 -f robo whatsapp ;;
  historico) tail -n 30 $PASTA/dados/postados.log ;;
  canais) $COMPOSE exec -T robo python -m services.whatsapp_service ;;
  reiniciar) $COMPOSE restart ;;
  *) $COMPOSE exec -T robo python robo.py "\$@" ;;
esac
SCRIPT
chmod +x /usr/local/bin/robo

echo "==> Construindo e ligando (a primeira vez demora alguns minutos)"
$COMPOSE up -d --build

echo
echo "Pronto. O robô está de pé, mas DESLIGADO até você rodar: robo ligar"
