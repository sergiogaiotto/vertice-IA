"""Use case: Persistência dinâmica em tabela.

Para módulos response_type='table': recebe um JSON do LLM e grava em uma tabela
cujo nome é {module_name}__{feature}. Achata objetos aninhados em dot notation
(cliente.nome → cliente_nome). Se surgirem chaves novas, faz ALTER TABLE para
adicionar colunas.

Inclui sempre colunas de auditoria:
    _id, _ts, _user_id, _username, _case_number, _transaction_id, _feature
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from app.adapters.db.postgres import connect, is_safe_ident, quote_ident


# Colunas de auditoria sempre presentes
_AUDIT_COLS = {
    "_id":             "TEXT PRIMARY KEY",
    "_ts":             "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    "_user_id":        "TEXT",
    "_username":       "TEXT",
    "_case_number":    "TEXT",
    "_transaction_id": "TEXT",
    "_feature":        "TEXT",
}


def _safe_ident(text: str, max_len: int = 60) -> str:
    """Sanitiza nome de tabela/coluna: minúsculas + [a-z0-9_], cap em max_len."""
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    if not text:
        text = "x"
    # Postgres aceita identificador iniciando com dígito apenas com aspas;
    # prefixamos com _ pra evitar surpresas.
    if text[0].isdigit():
        text = "_" + text
    return text[:max_len]


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Achata dict/list aninhados em dot notation.

    {"cliente": {"nome": "X"}} → {"cliente_nome": "X"}
    {"itens": ["a", "b"]}      → {"itens": '["a","b"]'}  (listas viram JSON)
    """
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = _safe_ident(f"{prefix}_{k}" if prefix else k)
            if isinstance(v, dict):
                out.update(_flatten(v, key))
            elif isinstance(v, list):
                # listas viram TEXT JSON (não achatamos arrays).
                out[key] = json.dumps(v, ensure_ascii=False, default=str)
            elif isinstance(v, (str, int, float, bool)) or v is None:
                out[key] = v
            else:
                out[key] = str(v)
    return out


def _infer_pg_type(value: Any) -> str:
    """Infere tipo PostgreSQL a partir do valor Python."""
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "BIGINT"
    if isinstance(value, float):
        return "DOUBLE PRECISION"
    return "TEXT"


class DynamicTableService:

    def table_name(self, module_name: str, feature: str) -> str:
        return _safe_ident(f"{module_name}__{feature}", max_len=60)

    async def _existing_columns(self, db, table: str) -> set[str]:
        rows = await db.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1",
            table,
        )
        return {r["column_name"] for r in rows}

    async def _table_exists(self, db, table: str) -> bool:
        return bool(await db.fetchval(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = $1",
            table,
        ))

    async def ensure_table(self, table: str, sample_data: dict[str, Any]) -> None:
        """Cria a tabela se não existir, com colunas de auditoria + colunas
        do sample. Em PG, `ADD COLUMN IF NOT EXISTS` é nativo — não precisa
        consultar a lista de colunas."""
        if not is_safe_ident(table):
            raise ValueError(f"nome de tabela inválido: {table}")
        async with connect() as db:
            exists = await self._table_exists(db, table)
            if not exists:
                cols_sql = [
                    f"{quote_ident(c)} {t}" for c, t in _AUDIT_COLS.items()
                ]
                for k, v in sample_data.items():
                    if k in _AUDIT_COLS:
                        continue
                    cols_sql.append(f"{quote_ident(k)} {_infer_pg_type(v)}")
                ddl = (
                    f"CREATE TABLE {quote_ident(table)} ("
                    + ", ".join(cols_sql)
                    + ")"
                )
                await db.execute(ddl)
                await db.execute(
                    f"CREATE INDEX IF NOT EXISTS "
                    f"{quote_ident('idx_' + table + '_ts')} "
                    f"ON {quote_ident(table)}(_ts DESC)"
                )
                return

            # tabela existe — adiciona colunas novas idempotentemente.
            for k, v in sample_data.items():
                if k in _AUDIT_COLS:
                    continue
                # ADD COLUMN IF NOT EXISTS evita race com outras réplicas.
                try:
                    await db.execute(
                        f"ALTER TABLE {quote_ident(table)} "
                        f"ADD COLUMN IF NOT EXISTS "
                        f"{quote_ident(k)} {_infer_pg_type(v)}"
                    )
                except Exception:
                    pass

    async def insert(
        self, table: str, data: dict[str, Any],
        user_id: str | None, username: str | None,
        case_number: str | None, transaction_id: str | None,
        feature: str | None,
    ) -> str:
        """Insere uma linha. Achata data, garante schema, NULL para chaves ausentes."""
        if not is_safe_ident(table):
            raise ValueError(f"nome de tabela inválido: {table}")
        flat = _flatten(data)
        await self.ensure_table(table, flat)

        row_id = uuid.uuid4().hex
        full = {
            "_id": row_id,
            "_user_id": user_id,
            "_username": username,
            "_case_number": case_number,
            "_transaction_id": transaction_id,
            "_feature": feature,
        }
        for k, v in flat.items():
            full[k] = v   # PG bool/int/float/str passa nativo via asyncpg

        cols = list(full.keys())
        placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
        cols_quoted = ", ".join(quote_ident(c) for c in cols)
        values = [full[c] for c in cols]

        async with connect() as db:
            await db.execute(
                f"INSERT INTO {quote_ident(table)} ({cols_quoted}) "
                f"VALUES ({placeholders})",
                *values,
            )
        return row_id

    async def list_rows(self, table: str, limit: int = 100) -> list[dict]:
        """Lista as últimas linhas de uma tabela dinâmica."""
        if not is_safe_ident(table):
            raise ValueError(f"nome de tabela inválido: {table}")
        async with connect() as db:
            cols = await self._existing_columns(db, table)
            if not cols:
                return []
            rows = await db.fetch(
                f"SELECT * FROM {quote_ident(table)} "
                "ORDER BY _ts DESC LIMIT $1",
                limit,
            )
            return [dict(r) for r in rows]

    async def table_info(self, table: str) -> dict:
        """Devolve metadados de uma tabela dinâmica."""
        if not is_safe_ident(table):
            return {"exists": False, "columns": [], "row_count": 0}
        async with connect() as db:
            if not await self._table_exists(db, table):
                return {"exists": False, "columns": [], "row_count": 0}
            col_rows = await db.fetch(
                "SELECT column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = $1 "
                "ORDER BY ordinal_position",
                table,
            )
            cols = [
                {"name": r["column_name"], "type": (r["data_type"] or "").upper()}
                for r in col_rows
            ]
            count = await db.fetchval(
                f"SELECT COUNT(*) FROM {quote_ident(table)}"
            )
            return {"exists": True, "columns": cols, "row_count": int(count or 0)}


_global = DynamicTableService()


def get_dynamic_table_service() -> DynamicTableService:
    return _global
