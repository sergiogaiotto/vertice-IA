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
from datetime import datetime
from typing import Any

from app.adapters.db.sqlite import connect


# Colunas de auditoria sempre presentes
_AUDIT_COLS = {
    "_id":             "TEXT PRIMARY KEY",
    "_ts":             "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
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
    # SQLite não aceita identificadores começando com dígito sem aspas;
    # com aspas duplas funciona, mas prefixamos com _ pra evitar surpresas
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
                # listas viram TEXT JSON (não achatamos arrays — colunas variariam por linha)
                out[key] = json.dumps(v, ensure_ascii=False, default=str)
            elif isinstance(v, (str, int, float, bool)) or v is None:
                out[key] = v
            else:
                out[key] = str(v)
    return out


def _infer_sqlite_type(value: Any) -> str:
    """Infere tipo SQLite a partir do valor Python."""
    if isinstance(value, bool):
        return "INTEGER"  # bool vira 0/1
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "TEXT"


def _normalize_value(value: Any) -> Any:
    """Converte bool para 0/1 (SQLite não tem bool nativo)."""
    if isinstance(value, bool):
        return 1 if value else 0
    return value


class DynamicTableService:

    def table_name(self, module_name: str, feature: str) -> str:
        return _safe_ident(f"{module_name}__{feature}", max_len=60)

    async def ensure_table(self, table: str, sample_data: dict[str, Any]) -> None:
        """Cria a tabela se não existir, com colunas de auditoria + colunas do sample."""
        async with connect() as db:
            cur = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                (table,),
            )
            exists = await cur.fetchone()
            if not exists:
                cols_sql = [f'"{c}" {t}' for c, t in _AUDIT_COLS.items()]
                for k, v in sample_data.items():
                    if k in _AUDIT_COLS:
                        continue
                    cols_sql.append(f'"{k}" {_infer_sqlite_type(v)}')
                ddl = f'CREATE TABLE "{table}" (' + ", ".join(cols_sql) + ")"
                await db.execute(ddl)
                # índice por timestamp pra consulta rápida
                await db.execute(
                    f'CREATE INDEX IF NOT EXISTS "idx_{table}_ts" ON "{table}"(_ts DESC)'
                )
                await db.commit()
                return

            # tabela existe — verifica se há colunas novas para ALTER
            cur = await db.execute(f'PRAGMA table_info("{table}")')
            existing_cols = {row[1] for row in await cur.fetchall()}
            new_cols = []
            for k, v in sample_data.items():
                if k not in existing_cols and k not in _AUDIT_COLS:
                    new_cols.append((k, _infer_sqlite_type(v)))
            for col_name, col_type in new_cols:
                try:
                    await db.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col_name}" {col_type}')
                except Exception:
                    # se já existe (race condition) ou outro erro, ignora
                    pass
            if new_cols:
                await db.commit()

    async def insert(
        self, table: str, data: dict[str, Any],
        user_id: str | None, username: str | None,
        case_number: str | None, transaction_id: str | None,
        feature: str | None,
    ) -> str:
        """Insere uma linha. Achata data, garante schema, NULL para chaves ausentes."""
        flat = _flatten(data)
        await self.ensure_table(table, flat)

        # monta dict completo com auditoria + dados
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
            full[k] = _normalize_value(v)

        cols = list(full.keys())
        placeholders = ", ".join(["?"] * len(cols))
        cols_quoted = ", ".join(f'"{c}"' for c in cols)
        values = [full[c] for c in cols]

        async with connect() as db:
            await db.execute(
                f'INSERT INTO "{table}" ({cols_quoted}) VALUES ({placeholders})',
                values,
            )
            await db.commit()
        return row_id

    async def list_rows(self, table: str, limit: int = 100) -> list[dict]:
        """Lista as últimas linhas de uma tabela dinâmica."""
        async with connect() as db:
            cur = await db.execute(f'PRAGMA table_info("{table}")')
            cols = [row[1] for row in await cur.fetchall()]
            if not cols:
                return []
            cur = await db.execute(
                f'SELECT * FROM "{table}" ORDER BY _ts DESC LIMIT ?', (limit,)
            )
            rows = await cur.fetchall()
            return [dict(zip(cols, r)) for r in rows]

    async def table_info(self, table: str) -> dict:
        """Devolve metadados de uma tabela dinâmica (existe, colunas, count)."""
        async with connect() as db:
            cur = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                (table,),
            )
            if not await cur.fetchone():
                return {"exists": False, "columns": [], "row_count": 0}
            cur = await db.execute(f'PRAGMA table_info("{table}")')
            cols = [{"name": row[1], "type": row[2]} for row in await cur.fetchall()]
            cur = await db.execute(f'SELECT COUNT(*) FROM "{table}"')
            count = int((await cur.fetchone())[0])
            return {"exists": True, "columns": cols, "row_count": count}


_global = DynamicTableService()


def get_dynamic_table_service() -> DynamicTableService:
    return _global
