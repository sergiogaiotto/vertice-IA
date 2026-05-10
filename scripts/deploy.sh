#!/usr/bin/env bash
# Vértice — deploy/atualização da stack de produção.
#
# Idempotente. Roda do diretório do repo, NO host (não dentro de container).
#   - valida que .env.production existe e tem secrets críticos preenchidos
#   - garante permissão 600 em .env.production
#   - faz git pull (se houver remote rastreado)
#   - build + up -d com Compose
#   - aguarda health do app
#   - imprime URL pública e dicas de comandos pós-deploy
#
# Variáveis ambiente opcionais:
#   COMPOSE_FILE  (default: docker-compose.yml)
#   ENV_FILE      (default: .env.production)
#   SKIP_PULL=1   pula git pull (deploy local sem alteração)

set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
ENV_FILE="${ENV_FILE:-.env.production}"

log()  { printf '\033[1;36m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[deploy]\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31m[deploy]\033[0m %s\n' "$*" >&2; exit 1; }

cd "$(dirname "$0")/.."   # raiz do repo

# ---------------------------------------------------------------- pré-checks
[ -f "$COMPOSE_FILE" ] || err "${COMPOSE_FILE} não encontrado. Você está na raiz do repo?"
[ -f "$ENV_FILE" ]     || err "${ENV_FILE} não existe. Crie a partir de .env.production.example"

# Permissão restritiva no env de produção (segredos!)
chmod 600 "$ENV_FILE"

# Validação rápida de placeholders não preenchidos.
if grep -E '^[A-Z_]+=TROCAR' "$ENV_FILE" >/dev/null; then
    grep -E '^[A-Z_]+=TROCAR' "$ENV_FILE" | sed 's/^/  /'
    err "valores TROCAR_* ainda presentes em ${ENV_FILE}. Edite antes de continuar."
fi

# Confirma que docker está acessível (deploy user no grupo docker?)
docker info >/dev/null 2>&1 || err "docker não acessível. Você está no grupo 'docker'? (newgrp docker / re-login)"

# ---------------------------------------------------------------- pull (opcional)
if [ -z "${SKIP_PULL:-}" ] && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if git remote get-url origin >/dev/null 2>&1; then
        log "atualizando código (git pull)..."
        git pull --ff-only || warn "git pull falhou — continuando com o estado atual"
    else
        log "sem remote 'origin' — pulando git pull"
    fi
else
    log "git pull pulado"
fi

# ---------------------------------------------------------------- build + up
log "build da imagem Vértice..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build vertice

log "subindo stack (caddy + vertice + postgres + pgbackup)..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --remove-orphans

# ---------------------------------------------------------------- aguarda health
log "aguardando app ficar saudável (healthcheck do Docker)..."
attempt=0
max_attempts=40   # ~2 min total (40 × 3s)
while [ $attempt -lt $max_attempts ]; do
    status="$(docker inspect --format='{{.State.Health.Status}}' vertice-app 2>/dev/null || echo 'starting')"
    case "$status" in
        healthy)   log "vertice-app: healthy ✓"; break ;;
        unhealthy) err "vertice-app: unhealthy. Veja: docker compose logs vertice" ;;
        *)         printf '.'; sleep 3; attempt=$((attempt+1)) ;;
    esac
done
[ $attempt -lt $max_attempts ] || err "timeout esperando healthy. Veja: docker compose logs vertice"

# ---------------------------------------------------------------- pós-deploy
DOMAIN="$(grep -E '^DOMAIN=' "$ENV_FILE" | cut -d= -f2)"
PUBLIC_HTTPS_PORT="$(grep -E '^PUBLIC_HTTPS_PORT=' "$ENV_FILE" | cut -d= -f2)"
PUBLIC_HTTPS_PORT="${PUBLIC_HTTPS_PORT:-8010}"
if [ "$PUBLIC_HTTPS_PORT" = "443" ]; then
    URL="https://${DOMAIN}"
else
    URL="https://${DOMAIN}:${PUBLIC_HTTPS_PORT}"
fi
cat <<EOF

──────────────────────────────────────────────────────────────────
  ✓ Deploy concluído

  URL:           ${URL}
  Login admin:   admin / (ADMIN_BOOTSTRAP_PASSWORD do .env.production)
                 Trocar IMEDIATAMENTE via UI.

  Comandos úteis:
    docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE} logs -f vertice
    docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE} logs -f caddy
    docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE} ps
    ./scripts/restore.sh --list                    # listar backups
    docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE} \\
         exec postgres psql -U vertice vertice    # console SQL

  Para atualizar o código:
    cd $(pwd) && ./scripts/deploy.sh

  TLS Let's Encrypt: emitido automaticamente no primeiro acesso a
  ${URL}. Verifique o DNS A do domínio aponta para o IP desta VPS
  e que as portas 80 (ACME) e ${PUBLIC_HTTPS_PORT} (HTTPS) estão liberadas
  no firewall.
──────────────────────────────────────────────────────────────────
EOF
