"""Fixtures globais — usam um banco PostgreSQL real para os testes.

Estratégia:
  * Usa o `DATABASE_URL` configurado (ou um default local) e cria/derruba
    UM SCHEMA dedicado por sessão de teste (`vertice_test_<pid>`).
  * Cada teste compartilha o schema, mas as tabelas são limpas com TRUNCATE
    em ordem reversa de dependência via fixture autouse `_reset_db_per_test`.
  * Schema é dropado (CASCADE) ao final da sessão.

O motivo de usar schema isolado em vez de `:memory:` é simples: queremos
exercitar exatamente o mesmo SQL que vai pra produção, incluindo JSONB,
TIMESTAMPTZ, ON CONFLICT etc. Não há equivalente in-memory de PostgreSQL.

Variável de ambiente esperada:
  TEST_DATABASE_URL — DSN para o servidor PG de teste. Se ausente, tenta
  postgresql://vertice:vertice@localhost:5432/vertice_test (compatível
  com docker-compose de dev).
"""

from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest


_DEFAULT_TEST_DSN = "postgresql://vertice:vertice@localhost:5432/vertice_test"


def _test_dsn() -> str:
    return os.environ.get("TEST_DATABASE_URL") or _DEFAULT_TEST_DSN


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def monkeypatch_session():
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="session", autouse=True)
def _isolated_test_schema(monkeypatch_session, event_loop):
    """Cria um schema dedicado por sessão de teste e direciona o app pra ele.

    O schema isolado evita colisão entre execuções paralelas do pytest e
    deixa o banco de produção/dev intacto — tudo é dropado no teardown.
    """
    schema = f"vertice_test_{uuid.uuid4().hex[:8]}"
    base_dsn = _test_dsn()
    # Sufixo `?options=-csearch_path=<schema>` configura search_path pra que
    # CREATE TABLE/INSERT/SELECT vão automaticamente para o schema isolado.
    sep = "&" if "?" in base_dsn else "?"
    test_dsn = f"{base_dsn}{sep}options=-csearch_path%3D{schema}"

    async def _setup():
        conn = await asyncpg.connect(base_dsn)
        try:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        finally:
            await conn.close()

    async def _teardown():
        # Tenta drop limpo. Se houver conexões dangling, força.
        conn = await asyncpg.connect(base_dsn)
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await conn.close()

    event_loop.run_until_complete(_setup())

    # Aponta o app para o schema isolado e força recarga do cache
    # `lru_cache` de `get_settings`.
    monkeypatch_session.setenv("DATABASE_URL", test_dsn)
    from app.config import get_settings
    get_settings.cache_clear()

    # Inicializa o pool / aplica schema.sql + seed.sql + bootstrap.
    from app.adapters.db.postgres import init_db, close_pool
    event_loop.run_until_complete(init_db())

    yield

    event_loop.run_until_complete(close_pool())
    event_loop.run_until_complete(_teardown())


# Lista de tabelas a limpar entre testes. Usa TRUNCATE em uma única
# instrução com CASCADE — muito mais rápido que DELETEs individuais.
# Tabelas dinâmicas (`{module}__{feature}`) são detectadas em runtime.
_KNOWN_TABLES = (
    "audit_events",
    "api_calls", "api_endpoints",
    "presentations",
    "raiox_analyses", "raiox_charts", "raiox_boards", "raiox_relationships",
    "radar_card_visibility", "radar_user_state",
    "finops_alerts", "finops_budgets", "finops_model_policies", "finops_ledger",
    "failsafe_actions",
    "churn_classifications", "churn_nodes",
    "analysis_cards", "contracts",
    "transcripts", "bko_cases",
    "prompts",
    "modules",
    "feature_access",   # matriz Funcionalidades por Perfil — regras vazavam
                        # entre testes e bloqueavam smoke de /radar com deny
                        # criado por teste de feature_access anterior.
    "user_roles", "users",
    # roles/permissions/permissions deixam o seed em paz: testes esperam ver
    # roles base.
)


@pytest.fixture(autouse=True)
async def _reset_db_per_test():
    """TRUNCATE entre testes — restaura idempotência sem dropar o schema.

    Mantém roles/permissions populados pelo seed (testes dependem do
    `bootstrap_root` enxergando a role 'root'). Re-roda o bootstrap dos
    módulos default + taxonomia churn ao final, pra que o estado entre
    testes seja igual ao do app fresco.
    """
    from app.adapters.db.postgres import connect, init_db
    async with connect() as db:
        # Tenta TRUNCATE direto. CASCADE cuida das FKs.
        joined = ", ".join(f'"{t}"' for t in _KNOWN_TABLES)
        try:
            await db.execute(f"TRUNCATE {joined} RESTART IDENTITY CASCADE")
        except asyncpg.exceptions.UndefinedTableError:
            # Schema ainda não tem tudo — init_db() cobre.
            pass

    # Reinjeta seeds + bootstrap de módulos/taxonomia.
    await init_db()

    yield
