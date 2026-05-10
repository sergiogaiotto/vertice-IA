#!/bin/sh
# Vértice — restore de um backup gerado por scripts/backup.sh.
#
# Uso (no host com docker compose já rodando):
#
#   # 1) listar backups disponíveis
#   ./scripts/restore.sh --list
#
#   # 2) restaurar um dump específico (DESTRUTIVO — sobrescreve o banco atual)
#   ./scripts/restore.sh /var/backups/postgres/vertice_20260510T030000Z.dump
#
# Para extrair um dump localmente do volume do Docker:
#   docker compose -f docker-compose.prod.yml --env-file .env.production \
#       cp pgbackup:/var/backups/postgres/<arquivo>.dump ./
#
# Restore funciona dentro do container pgbackup (que já tem pg_restore e
# acesso ao Postgres pela rede interna). Você pode rodar este script
# direto no host — ele invoca docker compose exec pgbackup.

set -eu

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.production}"
BACKUP_DIR_IN_CONTAINER="/var/backups/postgres"

usage() {
    cat <<EOF
Vértice — restore tool

  $0 --list                       lista backups disponíveis
  $0 <caminho-do-dump>            restaura o banco (DESTRUTIVO)

Variáveis ambiente opcionais:
  COMPOSE_FILE  (default: ${COMPOSE_FILE})
  ENV_FILE      (default: ${ENV_FILE})
EOF
}

if [ "$#" -eq 0 ] || [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi

dc() {
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

if [ "$1" = "--list" ]; then
    echo "Backups em ${BACKUP_DIR_IN_CONTAINER}:"
    dc exec -T pgbackup ls -lh "$BACKUP_DIR_IN_CONTAINER" || true
    exit 0
fi

DUMP_PATH="$1"

# Aceita caminho dentro do container (já em /var/backups/postgres) OU caminho
# do host (vamos copiar pra dentro).
if [ -f "$DUMP_PATH" ]; then
    echo "→ copiando ${DUMP_PATH} para o container pgbackup..."
    BASENAME="$(basename "$DUMP_PATH")"
    dc cp "$DUMP_PATH" "pgbackup:${BACKUP_DIR_IN_CONTAINER}/${BASENAME}"
    DUMP_IN_CONTAINER="${BACKUP_DIR_IN_CONTAINER}/${BASENAME}"
else
    DUMP_IN_CONTAINER="$DUMP_PATH"
fi

cat <<EOF

⚠️  ATENÇÃO — operação DESTRUTIVA
   O banco atual será DROPADO e recriado a partir de:
       ${DUMP_IN_CONTAINER}

   Pressione ENTER para confirmar ou Ctrl+C para cancelar.
EOF
read -r _

POSTGRES_USER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | cut -d= -f2)"
POSTGRES_DB="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | cut -d= -f2)"
: "${POSTGRES_USER:?POSTGRES_USER não encontrado em ${ENV_FILE}}"
: "${POSTGRES_DB:?POSTGRES_DB não encontrado em ${ENV_FILE}}"

echo "→ parando o app (mantém Postgres e pgbackup ativos)..."
dc stop vertice caddy

echo "→ recriando o banco ${POSTGRES_DB}..."
dc exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS ${POSTGRES_DB};"
dc exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE ${POSTGRES_DB};"

echo "→ rodando pg_restore..."
dc exec -T pgbackup pg_restore \
    --host=postgres \
    --username="$POSTGRES_USER" \
    --dbname="$POSTGRES_DB" \
    --no-owner --no-privileges \
    --jobs=4 \
    "$DUMP_IN_CONTAINER"

echo "→ subindo app + Caddy de volta..."
dc up -d vertice caddy

echo "✓ restore concluído"
