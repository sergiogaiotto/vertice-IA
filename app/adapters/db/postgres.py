"""Conexão e bootstrap PostgreSQL via asyncpg.

Substitui a antiga camada SQLite (aiosqlite) com foco em throughput:

  * Pool assíncrono (asyncpg.create_pool) com min/max calibráveis em runtime
    via env (`PG_POOL_MIN_SIZE`, `PG_POOL_MAX_SIZE`). Conexões warm reduzem
    latência em pico.
  * Prepared-statement cache por conexão — cada query parametrizada vira um
    statement preparado e reutilizável (asyncpg cuida do cache).
  * JSONB nativo: dicionários e listas Python serializam direto na ida/volta
    sem `json.dumps/json.loads` no Python. As colunas em `schema.sql` são
    declaradas como JSONB (não TEXT) — ganho operacional + indexação GIN.
  * Booleans nativos: `BOOLEAN` em vez de `INTEGER 0/1`.
  * Timestamps com timezone: `TIMESTAMPTZ` em todas as tabelas.

API exposta:

    pool() -> asyncpg.Pool                  # acesso direto (raro)
    connect() -> async ctx → asyncpg.Connection
    init_db() -> None                       # cria schema + seed + bootstrap
    close_pool() -> None                    # shutdown gracioso

Uso típico nos repositórios::

    async with connect() as db:
        row = await db.fetchrow("SELECT id FROM users WHERE username = $1", username)

A função `connect()` mantém o nome herdado do código aiosqlite para minimizar
churn nos call sites — internamente devolve uma conexão do pool, não uma
conexão nova.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any

import asyncpg

from app.config import get_settings
from app.core.domain.entities import Module, ModuleStatus, new_uuid

# IMPORTANT: do NOT capture `settings` at module load. Tests rely on
# monkeypatching DATABASE_URL after this module is imported (conftest's
# `_isolated_test_schema` fixture sets it to a per-session schema), and any
# value captured here would freeze the pre-test DSN. Always read settings
# inside the functions that need them.
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_SEED_PATH = Path(__file__).parent / "seed.sql"

# Pool global lazy. Inicializado em `init_db()` ou na primeira chamada a
# `connect()`. Único por processo — uvicorn `--workers N` cria N pools
# independentes (intencional para isolamento de connection lifetime).
_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


def _decode_timestamptz(value: str) -> _dt.datetime:
    """Converte texto ISO timestamptz do PG em datetime naive (UTC).

    Compatibilidade: o restante do app usa `datetime.utcnow()` (sempre naive
    em UTC). Asyncpg, por padrão, devolve `timestamptz` como `datetime`
    aware (`tzinfo=UTC`). Comparações entre aware e naive levantam TypeError
    em Python — então convertemos aqui no decoder, num único ponto.
    """
    parsed = _dt.datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return parsed


def _encode_timestamptz(value) -> str | None:
    """Aceita `datetime` naive (interpretado como UTC) ou aware. None passa."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.isoformat()
    return str(value)


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Configura a conexão recém-criada pelo pool.

    - Registra codec JSONB que aceita dict/list direto (sem json.dumps no caller).
    - Registra codec `timestamptz` que devolve `datetime` naive em UTC, para
      que o app continue comparando com `datetime.utcnow()` sem TypeError.
    - Define timezone UTC para queries que dependem de NOW().
    """
    # Codec JSONB: asyncpg já suporta nativamente, mas registrar explicitamente
    # garante que o encoder use ujson/json padrão e que None vire SQL NULL.
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda v: json.dumps(v, ensure_ascii=False, default=str) if v is not None else None,
        decoder=lambda v: json.loads(v) if v else None,
        schema="pg_catalog",
        format="text",
    )
    # Mesmo codec para JSON simples (caso alguma coluna não seja JSONB).
    await conn.set_type_codec(
        "json",
        encoder=lambda v: json.dumps(v, ensure_ascii=False, default=str) if v is not None else None,
        decoder=lambda v: json.loads(v) if v else None,
        schema="pg_catalog",
        format="text",
    )
    # Codec timestamptz → datetime naive em UTC.
    await conn.set_type_codec(
        "timestamptz",
        encoder=_encode_timestamptz,
        decoder=_decode_timestamptz,
        schema="pg_catalog",
        format="text",
    )
    # Define timezone consistente com o uso `datetime.utcnow()` no app.
    await conn.execute("SET TIME ZONE 'UTC'")


async def get_pool() -> asyncpg.Pool:
    """Retorna o pool global, inicializando-o sob lock se necessário."""
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is not None:  # outro coroutine inicializou enquanto esperávamos
            return _pool
        # Reload settings at pool creation time (see module-level note).
        s = get_settings()
        _pool = await asyncpg.create_pool(
            dsn=s.pg_dsn,
            min_size=s.pg_pool_min_size,
            max_size=s.pg_pool_max_size,
            max_inactive_connection_lifetime=s.pg_pool_max_inactive_connection_lifetime,
            command_timeout=s.pg_command_timeout,
            statement_cache_size=s.pg_statement_cache_size,
            init=_init_connection,
        )
        return _pool


def pool() -> asyncpg.Pool:
    """Versão síncrona — retorna o pool já inicializado, ou levanta erro.

    Útil para diagnóstico. Em código de runtime, prefira `await get_pool()`.
    """
    if _pool is None:
        raise RuntimeError(
            "Pool PostgreSQL não inicializado. Chame `await init_db()` no startup."
        )
    return _pool


def connect():
    """Retorna um async context manager que fornece uma conexão do pool.

    Equivalente em uso a `aiosqlite.connect(...)` — porém SEM abrir uma nova
    conexão a cada chamada: o pool reaproveita conexões warm. Por isso a
    chamada é barata e idiomática para qualquer query.
    """
    return _ConnectionContext()


class _ConnectionContext:
    """Wrapper assíncrono que pega/devolve conexão ao pool.

    Sob a hood, equivalente a `async with pool.acquire() as conn`. A conversão
    explícita facilita mock/stub em testes sem precisar conhecer o detalhe
    do pool.
    """

    __slots__ = ("_conn", "_acquire_cm")

    def __init__(self) -> None:
        self._conn: asyncpg.Connection | None = None
        self._acquire_cm = None

    async def __aenter__(self) -> asyncpg.Connection:
        p = await get_pool()
        self._acquire_cm = p.acquire()
        self._conn = await self._acquire_cm.__aenter__()
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        if self._acquire_cm is not None:
            await self._acquire_cm.__aexit__(exc_type, exc, tb)


async def close_pool() -> None:
    """Fecha o pool no shutdown da aplicação. Idempotente."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ---------------------------------------------------------------------------
# Bootstrap: schema + seed + módulos default
# ---------------------------------------------------------------------------


def _split_sql_statements(sql: str) -> list[str]:
    """Divide um arquivo .sql em statements para `asyncpg.execute`.

    asyncpg não tem `executescript()` — cada statement precisa ser executado
    individualmente. Limites são separados por `;` no fim de linha (com possível
    whitespace), respeitando blocos de comentário e strings.
    """
    # Implementação simples e correta para o nosso schema — sem dollar-quoted
    # strings ($$...$$), sem PL/pgSQL, sem ; literal dentro de string. Para
    # casos mais complexos seria preciso um tokenizador SQL completo.
    statements: list[str] = []
    buf: list[str] = []
    in_single_quote = False
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            # Mantém linha de comentário pra preservar formatação se for útil.
            buf.append(line)
            continue
        for ch in line:
            if ch == "'":
                in_single_quote = not in_single_quote
        buf.append(line)
        if not in_single_quote and line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


async def _exec_script(conn: asyncpg.Connection, sql: str) -> None:
    """Executa um script SQL multi-statement em uma única transação."""
    statements = _split_sql_statements(sql)
    async with conn.transaction():
        for stmt in statements:
            # Ignora linhas que sejam só comentário.
            content = "\n".join(
                ln for ln in stmt.splitlines() if not ln.strip().startswith("--")
            ).strip()
            if not content:
                continue
            await conn.execute(stmt)


async def init_db() -> None:
    """Cria schema, aplica seed e faz bootstrap dos módulos/taxonomia default.

    Idempotente: pode ser chamado em todo startup. As migrações usam
    `ADD COLUMN IF NOT EXISTS` (Postgres 9.6+) e `CREATE TABLE IF NOT EXISTS`,
    eliminando as PRAGMA table_info() check necessárias no SQLite.
    """
    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    seed = _SEED_PATH.read_text(encoding="utf-8")

    p = await get_pool()
    async with p.acquire() as conn:
        await _exec_script(conn, schema)
        await _exec_script(conn, seed)

        # Bootstrap módulos default — idempotente via `ON CONFLICT DO NOTHING`.
        defaults = [
            Module(
                id=new_uuid(),
                name="radar",
                endpoint_url="/api/radar/v1/process",
                status=ModuleStatus.active,
                config_params={"threshold": 0.7, "sanitization": True, "failsafe": False},
                description="Voz do Cliente — cards de análise sobre transcrições.",
                skill_path="app/skills/radar_intent.md",
            ),
            Module(
                id=new_uuid(),
                name="churn",
                endpoint_url="/api/churn/v1/process",
                status=ModuleStatus.active,
                config_params={"threshold": 0.65, "auto_grow_taxonomy": True},
                description="Classificador hierárquico de motivos de cancelamento.",
                skill_path="app/skills/churn_classifier.md",
            ),
        ]
        for m in defaults:
            await conn.execute(
                """
                INSERT INTO modules (id, name, endpoint_url, status, config_params,
                                     description, skill_path)
                VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6, $7)
                ON CONFLICT (name) DO NOTHING
                """,
                str(m.id), m.name, m.endpoint_url, m.status.value,
                m.config_params, m.description, m.skill_path,
            )

        # Bootstrap taxonomia churn raiz (idempotente).
        existing = await conn.fetchval("SELECT COUNT(*) FROM churn_nodes")
        if existing == 0:
            roots = [
                ("Preço", []),
                ("Qualidade do serviço", ["Sinal/cobertura", "Velocidade", "Quedas"]),
                ("Atendimento", ["Tempo de espera", "Falta de resolução"]),
                ("Concorrência", ["Oferta melhor", "Indicação de terceiros"]),
                ("Mudança de necessidade", []),
            ]
            for label, children in roots:
                rid = str(new_uuid())
                await conn.execute(
                    "INSERT INTO churn_nodes (id, label, parent_id, depth) "
                    "VALUES ($1::uuid, $2, NULL, 0)",
                    rid, label,
                )
                for c in children:
                    cid = str(new_uuid())
                    await conn.execute(
                        "INSERT INTO churn_nodes (id, label, parent_id, depth) "
                        "VALUES ($1::uuid, $2, $3::uuid, 1)",
                        cid, c, rid,
                    )


# ---------------------------------------------------------------------------
# Helpers compartilhados
# ---------------------------------------------------------------------------


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_safe_ident(name: str) -> bool:
    """Valida identificador SQL (a-z, 0-9, _) — usado em queries dinâmicas
    de `schema_service` e `dynamic_table_service` onde o nome de coluna/tabela
    precisa ser interpolado direto na string."""
    return bool(name) and len(name) <= 64 and bool(_IDENT_RE.match(name))


def quote_ident(name: str) -> str:
    """Quote seguro para identificador. Use APENAS após validar com is_safe_ident."""
    return '"' + name.replace('"', '""') + '"'


__all__ = [
    "close_pool",
    "connect",
    "get_pool",
    "init_db",
    "is_safe_ident",
    "pool",
    "quote_ident",
]
