#!/usr/bin/env bash
# Vértice — provisionamento inicial de uma VPS Hostinger (Ubuntu 22.04/24.04).
#
# Idempotente: rode quantas vezes quiser. O que faz:
#   1. apt update + pacotes essenciais (ca-certs, curl, gnupg, ufw, fail2ban)
#   2. Instala Docker Engine + plugin compose (repo oficial)
#   3. Cria usuário 'deploy' (se não existir) e adiciona ao grupo docker
#   4. Habilita UFW: 22, 80, 443. Bloqueia o resto.
#   5. Liga fail2ban com jail SSH default
#   6. Configura swap de 2GB se a VPS tem <4GB RAM (evita OOM no build)
#   7. Confirma versões instaladas
#
# Como usar (a partir do seu laptop):
#
#   ssh root@SEU_IP
#   curl -fsSL https://raw.githubusercontent.com/SEU_USER/SEU_REPO/main/scripts/install_docker.sh \
#        -o install_docker.sh
#   chmod +x install_docker.sh
#   ./install_docker.sh
#
# Após rodar:
#   - faça login como 'deploy' (ou continue como root)
#   - clone o repo: git clone https://github.com/SEU_USER/SEU_REPO.git
#   - rode: cd SEU_REPO && ./scripts/deploy.sh

set -euo pipefail

log() { printf '\033[1;36m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install]\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31m[install]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || err "rode como root (sudo -i ou ssh root@...)"

. /etc/os-release || err "/etc/os-release não encontrado"
case "${ID,,}" in
    ubuntu|debian) : ;;
    *) err "este script foi testado apenas em Ubuntu/Debian (você tem ${ID})" ;;
esac

log "atualizando apt..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    ufw \
    fail2ban \
    git \
    htop \
    unattended-upgrades

# ---------------------------------------------------------------- Docker
if ! command -v docker >/dev/null 2>&1; then
    log "instalando Docker Engine + plugin compose..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/${ID}/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${ID} $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list

    apt-get update -qq
    apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    systemctl enable --now docker
else
    log "Docker já instalado ($(docker --version))"
fi

# Atalho compose já vem como plugin. Verifica:
docker compose version >/dev/null 2>&1 || err "docker compose plugin não disponível"

# ---------------------------------------------------------------- Usuário deploy
DEPLOY_USER="${DEPLOY_USER:-deploy}"
if id "$DEPLOY_USER" >/dev/null 2>&1; then
    log "usuário '${DEPLOY_USER}' já existe"
else
    log "criando usuário '${DEPLOY_USER}'..."
    adduser --disabled-password --gecos "" "$DEPLOY_USER"
fi

usermod -aG docker "$DEPLOY_USER"
# Permite que 'deploy' use sudo sem senha — facilita CI/CD. Comente se
# preferir senha.
echo "${DEPLOY_USER} ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/90-${DEPLOY_USER}
chmod 440 /etc/sudoers.d/90-${DEPLOY_USER}

# Copia ~/.ssh/authorized_keys do root para o deploy (assumindo que você
# já entrou via chave SSH como root). Operação idempotente.
if [ -f /root/.ssh/authorized_keys ]; then
    log "copiando authorized_keys do root → ${DEPLOY_USER}..."
    install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/${DEPLOY_USER}/.ssh"
    install -m 600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" \
        /root/.ssh/authorized_keys "/home/${DEPLOY_USER}/.ssh/authorized_keys"
fi

# ---------------------------------------------------------------- Firewall
# Portas:
#   22       SSH
#   80       HTTP — necessário para ACME HTTP-01 challenge (Let's Encrypt)
#            + redirect 308 para a porta HTTPS pública
#   8010     HTTPS pública do Vértice (configurável em .env.production
#            via PUBLIC_HTTPS_PORT). Aberta também em UDP para HTTP/3.
PUBLIC_HTTPS_PORT="${PUBLIC_HTTPS_PORT:-8010}"
log "configurando UFW (22, 80, ${PUBLIC_HTTPS_PORT})..."
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP (ACME challenge + redirect)'
ufw allow "${PUBLIC_HTTPS_PORT}/tcp" comment 'HTTPS Vértice'
ufw allow "${PUBLIC_HTTPS_PORT}/udp" comment 'HTTP/3 QUIC'
ufw --force enable
ufw status verbose | sed 's/^/  /'

# ---------------------------------------------------------------- fail2ban
log "habilitando fail2ban (jail SSH default)..."
systemctl enable --now fail2ban

# ---------------------------------------------------------------- Swap
ram_mb=$(free -m | awk '/^Mem:/{print $2}')
if [ "$ram_mb" -lt 4096 ] && ! swapon --show | grep -q '^/swapfile'; then
    log "RAM=${ram_mb}MB < 4GB — criando 2GB de swap em /swapfile..."
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    sysctl -w vm.swappiness=10
    grep -q 'vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf
fi

# ---------------------------------------------------------------- Atualizações
log "habilitando unattended-upgrades (security only)..."
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true

# ---------------------------------------------------------------- Resumo
cat <<EOF

──────────────────────────────────────────────────────────────────
  ✓ Provisionamento concluído

  Docker:        $(docker --version)
  Compose:       $(docker compose version --short 2>/dev/null || echo '?')
  Usuário:       ${DEPLOY_USER} (com sudo NOPASSWD e grupo docker)
  Firewall:      ufw — 22/80/443 abertos
  Fail2ban:      ativo (jail sshd)
  Swap:          $(swapon --show=NAME,SIZE --noheadings || echo 'sem swap')

  Próximo passo:
    su - ${DEPLOY_USER}
    git clone <url-do-repo> vertice
    cd vertice
    cp .env.production.example .env.production
    chmod 600 .env.production
    nano .env.production              # preencher TROCAR_*
    ./scripts/deploy.sh
──────────────────────────────────────────────────────────────────
EOF
