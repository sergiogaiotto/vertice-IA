#!/bin/sh
# Vértice — entrypoint shim do container.
#
# Faz seed inicial do volume de skills (app/skills/) quando vazio, depois
# entrega controle pro CMD (uvicorn).
#
# Por que: o volume nomeado `vertice_skills` persiste skills criados via UI
# entre redeploys. Mas no PRIMEIRO boot o volume é vazio — copiamos os
# skills "shipped" no repo (snapshot em /opt/skills_seed) para que o app
# nunca suba sem skills básicos.

set -eu

SKILLS_DIR="${APP_HOME:-/app}/app/skills"
SEED_DIR="/opt/skills_seed"

# Garante que o diretório existe (mount cria, mas init container pode falhar)
mkdir -p "$SKILLS_DIR"

# Seed apenas se o diretório está vazio (primeiro boot).
# Usa `ls -A` (não inclui . e ..) — se devolver nada, vazio.
if [ -z "$(ls -A "$SKILLS_DIR" 2>/dev/null || true)" ]; then
    if [ -d "$SEED_DIR" ] && [ -n "$(ls -A "$SEED_DIR" 2>/dev/null || true)" ]; then
        printf '[entrypoint] seedando skills de %s para %s\n' "$SEED_DIR" "$SKILLS_DIR" >&2
        # `cp -r DIR/.` copia o conteúdo, não a pasta. -p preserva permissões.
        cp -rp "$SEED_DIR/." "$SKILLS_DIR/"
    else
        printf '[entrypoint] skills_seed vazio ou inexistente — skills/ permanece vazio\n' >&2
    fi
else
    printf '[entrypoint] skills/ já populado — sem seed\n' >&2
fi

# Passa o controle para o CMD do Dockerfile.
exec "$@"
