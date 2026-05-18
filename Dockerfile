# syntax=docker/dockerfile:1.7
#
# Vértice — imagem de produção.
# Multi-stage: 1) builder com toolchain pra compilar wheels nativos
# (asyncpg, bcrypt, pandas), 2) runtime slim sem compilador.
# Final: ~250MB, usuário não-root, healthcheck embutido.

# ---------- Stage 1: builder ----------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Toolchain para wheels nativos. libpq-dev para asyncpg fallback.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libpq-dev \
        libffi-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copia só requirements primeiro pra aproveitar cache do Docker quando
# código muda mas deps não.
COPY requirements.txt .

# Docling depende de PyTorch. A wheel padrão de torch no PyPI (Linux) traz
# CUDA embutido e puxa ~2GB de pacotes nvidia-* como dependências transitivas
# (nvidia-cublas, nvidia-cudnn, nvidia-cuda-runtime, triton, etc.). Como esta
# imagem roda CPU-only, forçamos a wheel CPU do índice oficial do PyTorch
# ANTES do `pip wheel -r requirements.txt`, satisfazendo a dependência sem
# arrastar o stack CUDA. `--extra-index-url` no segundo comando permite que
# outras libs ML eventuais também resolvam pelo índice CPU se precisarem.
RUN pip install --upgrade pip \
 && pip wheel --wheel-dir /wheels \
        --index-url https://download.pytorch.org/whl/cpu \
        torch \
 && pip wheel --wheel-dir /wheels \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.txt


# ---------- Stage 2: runtime ----------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_HOME=/app \
    PORT=8000 \
    UVICORN_WORKERS=2

# Bibliotecas runtime apenas (sem -dev). libpq5 cobre o asyncpg em fallback;
# curl para o HEALTHCHECK; tini para PID 1 limpo (graceful shutdown).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        tini \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --shell /bin/bash --uid 10001 vertice

WORKDIR ${APP_HOME}

# Instala wheels gerados no builder — sem rede, sem compilador.
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
 && rm -rf /wheels

# Copia o código já com owner correto. .dockerignore mantém imagem enxuta.
COPY --chown=vertice:vertice . .

# Diretório opcional para artefatos efêmeros que o app pode escrever
# (downloads de presentations, uploads etc.). Mantemos no FS do container —
# ephemeral por design; persistência real fica no Postgres.
RUN mkdir -p ${APP_HOME}/data \
 && chown -R vertice:vertice ${APP_HOME}/data

# Skills "shipped" no repo são copiados para um diretório read-only ao lado
# de app/. No primeiro boot, o entrypoint copia-as para app/skills/ (que é
# um volume nomeado em prod). Assim:
#   - skills criados via UI persistem entre redeploys (estão no volume);
#   - skills novos do repo entram só se o volume estiver vazio (primeiro
#     boot) — o operador limpa o volume manualmente quando quiser sync.
RUN mkdir -p /opt/skills_seed \
 && cp -r ${APP_HOME}/app/skills/. /opt/skills_seed/ 2>/dev/null || true \
 && chown -R vertice:vertice /opt/skills_seed

# Entrypoint shim: seed skills no primeiro boot, depois exec uvicorn.
# `sed` defensivo: se o checkout deste repo trouxe o script com CRLF
# (cenário comum em Windows + git autocrlf), normaliza para LF — caso
# contrário tini falha com "exec ...: No such file or directory" no
# shebang `#!/bin/sh\r`.
COPY --chown=vertice:vertice scripts/docker_entrypoint.sh /usr/local/bin/docker_entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/docker_entrypoint.sh \
 && chmod +x /usr/local/bin/docker_entrypoint.sh

USER vertice

EXPOSE 8000

# Healthcheck consumido pelo `depends_on: condition: service_healthy` do
# compose. Tem janela de 30s para o pool asyncpg subir.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# tini como PID 1 — evita PID 1 zombie reaping problems com uvicorn workers.
# Encadeia o entrypoint que faz seed dos skills antes do uvicorn.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker_entrypoint.sh"]

# Workers configuráveis via env. Default 2 cabe em VPS small (2 vCPU).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers ${UVICORN_WORKERS} --proxy-headers --forwarded-allow-ips='*'"]
