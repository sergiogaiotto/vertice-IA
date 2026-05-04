"""Conexão e bootstrap do SQLite assíncrono."""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite

from app.config import get_settings
from app.core.domain.entities import Module, ModuleStatus, new_uuid

settings = get_settings()

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_SEED_PATH = Path(__file__).parent / "seed.sql"


def get_db_path() -> Path:
    p = settings.db_path
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def connect() -> aiosqlite.Connection:
    return aiosqlite.connect(str(get_db_path()))


async def init_db() -> None:
    """Cria schema, aplica seed e bootstrap do admin + módulos default."""
    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    seed = _SEED_PATH.read_text(encoding="utf-8")

    async with connect() as db:
        await db.executescript(schema)
        await db.executescript(seed)

        # ---- migração idempotente: prompts.module_names (JSON array) ----
        # adiciona coluna se não existir e popula a partir de module_name legado
        cur = await db.execute("PRAGMA table_info(prompts)")
        cols = {row[1] for row in await cur.fetchall()}
        if "module_names" not in cols:
            await db.execute("ALTER TABLE prompts ADD COLUMN module_names TEXT")
            await db.execute(
                "UPDATE prompts SET module_names = '[\"' || module_name || '\"]' "
                "WHERE module_names IS NULL AND module_name IS NOT NULL AND module_name != ''"
            )
            await db.commit()

        # tabelas BKO (bko_cases, transcripts) já são criadas pelo schema.sql
        # via CREATE TABLE IF NOT EXISTS — sem migração extra necessária.

        # ---- migração idempotente: presentations.visuals ----
        cur = await db.execute("PRAGMA table_info(presentations)")
        pres_cols = {row[1] for row in await cur.fetchall()}
        if pres_cols and "visuals" not in pres_cols:
            await db.execute("ALTER TABLE presentations ADD COLUMN visuals TEXT")
            await db.commit()

        # ---- migração idempotente: modules.response_type / response_config ----
        cur = await db.execute("PRAGMA table_info(modules)")
        mod_cols = {row[1] for row in await cur.fetchall()}
        if mod_cols and "response_type" not in mod_cols:
            await db.execute("ALTER TABLE modules ADD COLUMN response_type TEXT DEFAULT 'text'")
            await db.commit()
        if mod_cols and "response_config" not in mod_cols:
            await db.execute("ALTER TABLE modules ADD COLUMN response_config TEXT")
            await db.commit()

        # ---- migração idempotente: raiox_charts.skill_path ----
        cur = await db.execute("PRAGMA table_info(raiox_charts)")
        rc_cols = {row[1] for row in await cur.fetchall()}
        if rc_cols and "skill_path" not in rc_cols:
            await db.execute("ALTER TABLE raiox_charts ADD COLUMN skill_path TEXT")
        await db.commit()

        # ---- migração idempotente: raiox_boards.allowed_roles / allowed_departments ----
        cur = await db.execute("PRAGMA table_info(raiox_boards)")
        rb_cols = {row[1] for row in await cur.fetchall()}
        if rb_cols and "allowed_roles" not in rb_cols:
            await db.execute("ALTER TABLE raiox_boards ADD COLUMN allowed_roles TEXT")
        if rb_cols and "allowed_departments" not in rb_cols:
            await db.execute("ALTER TABLE raiox_boards ADD COLUMN allowed_departments TEXT")
        await db.commit()

        # ---- migração idempotente: dimensões finops modernas no ledger ----
        # Cada dimensão é nullable: gravações antigas continuam válidas. A UI
        # do Cockpit FinOps trata NULL como "sem rateio" (bucket 'outros').
        cur = await db.execute("PRAGMA table_info(finops_ledger)")
        fin_cols = {row[1] for row in await cur.fetchall()}
        _NEW_LEDGER_COLS = [
            ("domain",        "TEXT"),
            ("product",       "TEXT"),
            ("agent",         "TEXT"),
            ("flow",          "TEXT"),
            ("prompt_id",     "TEXT"),
            ("integration",   "TEXT"),
            ("environment",   "TEXT DEFAULT 'production'"),
            ("latency_ms",    "REAL"),
            ("storage_bytes", "INTEGER"),
        ]
        for col, ddl in _NEW_LEDGER_COLS:
            if col not in fin_cols:
                await db.execute(f"ALTER TABLE finops_ledger ADD COLUMN {col} {ddl}")
        await db.commit()

        # Índices nas colunas novas — só agora, depois das colunas existirem.
        # Necessário ficar fora do schema.sql porque CREATE INDEX falha em
        # bancos pré-existentes (a tabela existe sem as colunas).
        await db.execute("CREATE INDEX IF NOT EXISTS idx_finops_domain ON finops_ledger(domain)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_finops_agent ON finops_ledger(agent)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_finops_environment ON finops_ledger(environment)")
        await db.commit()

        # migração idempotente: campos padrão do cadastro de usuário
        cur = await db.execute("PRAGMA table_info(users)")
        user_cols = {row[1] for row in await cur.fetchall()}
        for col in ["full_name", "email", "phone", "department", "title"]:
            if col not in user_cols:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT ''")
        await db.commit()

        # bootstrap módulos default
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
            cur = await db.execute("SELECT id FROM modules WHERE name = ?", (m.name,))
            if not await cur.fetchone():
                await db.execute(
                    "INSERT INTO modules (id, name, endpoint_url, status, config_params, description, skill_path) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(m.id),
                        m.name,
                        m.endpoint_url,
                        m.status.value,
                        json.dumps(m.config_params),
                        m.description,
                        m.skill_path,
                    ),
                )

        # bootstrap taxonomia churn raiz (idempotente)
        cur = await db.execute("SELECT COUNT(*) FROM churn_nodes")
        count = (await cur.fetchone())[0]
        if count == 0:
            roots = [
                ("Preço", []),
                ("Qualidade do serviço", ["Sinal/cobertura", "Velocidade", "Quedas"]),
                ("Atendimento", ["Tempo de espera", "Falta de resolução"]),
                ("Concorrência", ["Oferta melhor", "Indicação de terceiros"]),
                ("Mudança de necessidade", []),
            ]
            for label, children in roots:
                rid = str(new_uuid())
                await db.execute(
                    "INSERT INTO churn_nodes (id, label, parent_id, depth) VALUES (?, ?, NULL, 0)",
                    (rid, label),
                )
                for c in children:
                    cid = str(new_uuid())
                    await db.execute(
                        "INSERT INTO churn_nodes (id, label, parent_id, depth) VALUES (?, ?, ?, 1)",
                        (cid, c, rid),
                    )

        await db.commit()
