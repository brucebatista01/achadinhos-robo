#!/usr/bin/env bash
# Instala ou atualiza o Robô de Achadinhos numa VPS Ubuntu (22.04 ou 24.04).
#
# Uso, como root:
#   bash instalar_vps.sh
#
# Pode rodar de novo quantas vezes quiser: na segunda vez, só atualiza o
# código e reinicia o app (o .env e as peças geradas são mantidos).

set -euo pipefail

REPOSITORIO="https://github.com/brucebatista01/sistema-achadinhos.git"
PASTA="/opt/achadinhos"

echo "==> Instalando Docker, Git, firewall e proteções"
apt-get update -qq
apt-get install -y -qq docker.io docker-compose-v2 git ufw fail2ban unattended-upgrades >/dev/null
systemctl enable --now docker fail2ban >/dev/null

echo "==> Liberando só SSH e web no firewall"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

# Atualizações de segurança do Ubuntu instaladas automaticamente.
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null

# Login por SENHA desligado: só entra quem tem uma chave SSH cadastrada.
# Só fazemos isso se já houver chave, para ninguém ficar trancado para fora.
# O nome começa com 00 porque o SSH usa a PRIMEIRA configuração que encontra
# (a da hospedagem costuma vir num arquivo 50-...).
if [ -s /root/.ssh/authorized_keys ]; then
  printf '%s\n' "PasswordAuthentication no" "KbdInteractiveAuthentication no" \
    "PermitRootLogin prohibit-password" > /etc/ssh/sshd_config.d/00-achadinhos.conf
  sshd -t && systemctl reload ssh
  echo "==> Login por senha desligado (só chave SSH)"
fi

echo "==> Baixando o código"
if [ -d "$PASTA/.git" ]; then
  git -C "$PASTA" pull --ff-only
else
  git clone -q "$REPOSITORIO" "$PASTA"
fi
cd "$PASTA"

# A pasta das peças precisa pertencer ao usuário do container (id 1000).
mkdir -p output
chown 1000:1000 output

# Memória de reserva (swap): evita o app ser derrubado num pico de uso
# em VPS com pouca RAM.
if ! swapon --show | grep -q /swapfile; then
  echo "==> Criando 2 GB de memória de reserva (swap)"
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi

if [ ! -f .env ]; then
  cp .env.exemplo .env
  chmod 600 .env
  echo
  echo "Criei o $PASTA/.env. Preencha as chaves com:  nano $PASTA/.env"
  echo "Depois rode este script de novo."
  exit 0
fi

# Sem domínio próprio, usa o IP da VPS pelo sslip.io (gratuito).
if ! grep -qE '^DOMINIO=.+' .env; then
  IP=$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')
  echo "DOMINIO=${IP//./-}.sslip.io" >> .env
fi

echo "==> Construindo e ligando o app (a primeira vez demora alguns minutos)"
docker compose up -d --build

DOMINIO=$(grep -E '^DOMINIO=' .env | cut -d= -f2)
echo
echo "Pronto! O app responde em https://$DOMINIO/p/<APP_TOKEN>/"
