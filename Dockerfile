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

RUN pip install --upgrade pip \
 && pip wheel --wheel-dir /wheels -r requirements.txt


# ---------- Stage 2: runtime ----------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_HOME=/app \
    PORT=8000

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

USER vertice

EXPOSE 8000

# Healthcheck consumido pelo `depends_on: condition: service_healthy` do
# compose. Tem janela de 30s para o pool asyncpg subir.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# tini como PID 1 — evita PID 1 zombie reaping problems com uvicorn workers.
ENTRYPOINT ["/usr/bin/tini", "--"]

# 2 workers é um ponto de partida razoável para VPS small (2 vCPU). Em
# produção, ajustar via env CMD: docker compose run com --workers N.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 2 --proxy-headers --forwarded-allow-ips='*'"]
