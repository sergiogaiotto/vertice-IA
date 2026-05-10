#!/bin/sh
# Vértice — backup loop para o container `pgbackup` do compose de produção.
#
# Estratégia:
#   - Roda em foreground (PID 1 do container) num while-true.
#   - A cada iteração, calcula segundos até o próximo BACKUP_AT_HOUR_UTC.
#   - Acorda nesse horário, dispara pg_dump custom-format (-Fc) compactado.
#   - Aplica retenção (BACKUP_KEEP_DAYS) com find -delete.
#
# Custom-format (-Fc) é melhor que SQL plain porque:
#   - Já vem comprimido (níveis ajustáveis, default 6).
#   - Permite restore seletivo via pg_restore -t tabela.
#   - Independe da ordem de DDL — pg_restore resolve dependências.
#
# Para restore, ver scripts/restore.sh.

set -eu

: "${PGHOST:?PGHOST não definido}"
: "${PGUSER:?PGUSER não definido}"
: "${PGPASSWORD:?PGPASSWORD não definido}"
: "${PGDATABASE:?PGDATABASE não definido}"

BACKUP_DIR="${BACKUP_DIR:-/var/backups/postgres}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"
BACKUP_AT_HOUR_UTC="${BACKUP_AT_HOUR_UTC:-3}"

mkdir -p "$BACKUP_DIR"

log() {
    printf '%s [pgbackup] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"
}

dump_now() {
    ts="$(date -u '+%Y%m%dT%H%M%SZ')"
    out="${BACKUP_DIR}/vertice_${ts}.dump"
    tmp="${out}.tmp"
    log "iniciando pg_dump → ${out}"
    if pg_dump --format=custom --compress=6 --file="$tmp" "$PGDATABASE"; then
        mv "$tmp" "$out"
        size="$(du -h "$out" | cut -f1)"
        log "pg_dump OK ($size)"
    else
        rm -f "$tmp"
        log "ERRO no pg_dump"
        return 1
    fi

    # Retenção: apaga dumps mais velhos que BACKUP_KEEP_DAYS
    if [ "$BACKUP_KEEP_DAYS" -gt 0 ]; then
        log "removendo backups com mais de ${BACKUP_KEEP_DAYS} dias"
        find "$BACKUP_DIR" -name 'vertice_*.dump' -type f \
             -mtime +"$BACKUP_KEEP_DAYS" -print -delete | \
             sed 's/^/  removido: /' || true
    fi
}

# Garante que o Postgres aceita conexão antes do primeiro tick — evita
# crashloop bonito caso o serviço suba antes do banco.
wait_pg() {
    log "aguardando Postgres em ${PGHOST}:${PGPORT:-5432}..."
    until pg_isready -h "$PGHOST" -p "${PGPORT:-5432}" -U "$PGUSER" -d "$PGDATABASE" >/dev/null 2>&1; do
        sleep 5
    done
    log "Postgres pronto"
}

# Calcula segundos até o próximo BACKUP_AT_HOUR_UTC. Se já passou hoje,
# vai pra amanhã.
seconds_until_next_run() {
    now_h="$(date -u '+%H')"
    now_m="$(date -u '+%M')"
    now_s="$(date -u '+%S')"
    target_h="$BACKUP_AT_HOUR_UTC"

    # Segundos desde 00:00 hoje
    now_sec=$(( now_h * 3600 + now_m * 60 + now_s ))
    target_sec=$(( target_h * 3600 ))

    if [ "$now_sec" -lt "$target_sec" ]; then
        echo $(( target_sec - now_sec ))
    else
        # já passou — esperar até amanhã
        echo $(( 86400 - now_sec + target_sec ))
    fi
}

trap 'log "encerrando pgbackup"; exit 0' INT TERM

wait_pg

# Backup inicial assim que o container sobe (útil para recuperar de
# desligamento prolongado).
log "rodando backup inicial..."
dump_now || log "backup inicial falhou — será tentado novamente no próximo ciclo"

while true; do
    sleep_for="$(seconds_until_next_run)"
    log "próximo backup em ${sleep_for}s (alvo: ${BACKUP_AT_HOUR_UTC}:00 UTC)"
    sleep "$sleep_for"
    dump_now || log "ciclo de backup falhou — continuando"
done
