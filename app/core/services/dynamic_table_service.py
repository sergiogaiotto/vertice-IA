"""Use case: Persistência dinâmica em tabela.

Para módulos response_type='table': recebe um JSON do LLM e grava em uma tabela
cujo nome é {module_name}__{feature}. Achata objetos aninhados em dot notation
(cliente.nome → cliente_nome). Se surgirem chaves novas, faz ALTER TABLE para
adicionar colunas.

──────────────────────────────────────────────────────────────────────────────
Por que TODAS as colunas achatadas são TEXT (e não tipadas por inferência)
──────────────────────────────────────────────────────────────────────────────
A fonte destes dados é a saída JSON de um LLM. O tipo de uma mesma chave NÃO é
estável entre execuções: `valor_conta` pode chegar como `34.9` (float) numa
transcrição, `"34,90"` (string) em outra, `null` numa terceira e
`"não identificada"` numa quarta — mesmo com temperature=0 e instrução de JSON
estrito. Um tipo SQL fixo por coluna é incompatível com essa realidade.

A versão anterior inferia o tipo da coluna a partir do PRIMEIRO valor visto e o
congelava para sempre (via `ADD COLUMN IF NOT EXISTS`, que nunca revê o tipo).
Quando um valor de tipo Python diferente chegava, o asyncpg recusava o bind:

    DataError: invalid input for query argument $N: 34.9 (expected str, got float)
    DataError: invalid input for query argument $N: 'N/A' (... expected integer)

Correção: as colunas achatadas são sempre TEXT e todo valor é serializado para
uma representação textual estável no INSERT. Isso elimina a classe inteira de
erros de type-drift sem perder o dado (a stringificação é lossless).

Para NÃO perder fidelidade (estrutura aninhada, arrays, tipos originais), toda
linha guarda também o JSON estruturado bruto na coluna JSONB `_raw`. Ela é a
fonte de verdade para reprocessamento/consultas tipadas; as colunas TEXT servem
à navegação tabular, aos gráficos (que fazem `CAST(col AS DOUBLE PRECISION)`
explícito) e ao text-to-SQL.

Tabelas legadas (criadas antes desta correção, com colunas tipadas) se
auto-curam no próximo insert: colunas não-auditoria que não sejam TEXT são
alargadas para TEXT via `ALTER COLUMN ... TYPE TEXT USING col::text`, e a coluna
`_raw` é adicionada retroativamente.

Inclui sempre colunas de auditoria:
    _id, _ts, _user_id, _username, _case_number, _transaction_id, _feature, _raw
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from app.adapters.db.postgres import connect, is_safe_ident, quote_ident


# Colunas de auditoria sempre presentes. NÃO inclui `_raw` de propósito: este
# dict é compartilhado com o `xlsx_import_service`, que monta uma lista fixa de
# valores de auditoria posicionalmente — adicionar um item aqui quebraria aquele
# INSERT. A coluna `_raw` (específica de módulos response_type='table') é tratada
# separadamente via `_RAW_COL` abaixo.
_AUDIT_COLS = {
    "_id":             "TEXT PRIMARY KEY",
    "_ts":             "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    "_user_id":        "TEXT",
    "_username":       "TEXT",
    "_case_number":    "TEXT",
    "_transaction_id": "TEXT",
    "_feature":        "TEXT",
}

# Coluna JSONB com o payload estruturado original (fonte de verdade / fidelidade).
# Fora de `_AUDIT_COLS` para não afetar o xlsx_import_service (ver nota acima).
_RAW_COL = "_raw"

# Nomes que nunca devem ser alargados para TEXT nem tratados como coluna de dado.
# `_raw` (JSONB) e as colunas de auditoria tipadas (`_ts` TIMESTAMPTZ) precisam
# manter o tipo declarado.
_RESERVED_COLS = set(_AUDIT_COLS) | {_RAW_COL}


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


def _to_text(value: Any) -> str | None:
    """Serializa um escalar Python para a representação textual estável gravada
    nas colunas TEXT. `None` vira SQL NULL; bool vira 'true'/'false' (JSON);
    números viram sua repr decimal; strings passam intactas.

    Objetos/listas não deveriam chegar aqui (o `_flatten` já os converteu), mas
    caem em JSON por segurança.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, default=str)


class DynamicTableService:

    def table_name(self, module_name: str, feature: str) -> str:
        return _safe_ident(f"{module_name}__{feature}", max_len=60)

    async def _existing_columns(self, db, table: str) -> dict[str, str]:
        """Mapa {coluna: data_type} da tabela no schema corrente (search_path).

        Usa `current_schema()` — o mesmo schema onde um `CREATE TABLE` sem
        qualificação criaria a tabela — em vez de fixar `'public'`. Fixar
        'public' quebrava sob qualquer search_path customizado (ex.: o schema
        isolado dos testes), fazendo a checagem de existência falhar e o
        `CREATE TABLE` ser reemitido (DuplicateTableError)."""
        rows = await db.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = $1",
            table,
        )
        return {r["column_name"]: (r["data_type"] or "").lower() for r in rows}

    async def _table_exists(self, db, table: str) -> bool:
        return bool(await db.fetchval(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = $1",
            table,
        ))

    async def ensure_table(self, table: str, sample_data: dict[str, Any]) -> None:
        """Garante que a tabela exista com o schema necessário.

        - Não existe: cria com colunas de auditoria + `_raw` JSONB + uma coluna
          TEXT por chave do sample.
        - Existe: adiciona `_raw` se faltar (tabelas legadas), alarga qualquer
          coluna de dado ainda tipada para TEXT (auto-cura contra type-drift) e
          adiciona colunas TEXT novas idempotentemente.

        As colunas de dado são SEMPRE TEXT — ver a docstring do módulo."""
        if not is_safe_ident(table):
            raise ValueError(f"nome de tabela inválido: {table}")
        async with connect() as db:
            if not await self._table_exists(db, table):
                cols_sql = [f"{quote_ident(c)} {t}" for c, t in _AUDIT_COLS.items()]
                cols_sql.append(f"{quote_ident(_RAW_COL)} JSONB")
                for k in sample_data:
                    if k in _RESERVED_COLS:
                        continue
                    cols_sql.append(f"{quote_ident(k)} TEXT")
                # IF NOT EXISTS + current_schema() tornam a criação idempotente
                # e segura contra corrida entre réplicas no boot.
                ddl = (
                    f"CREATE TABLE IF NOT EXISTS {quote_ident(table)} ("
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

            # Tabela existe — reconcilia o schema.
            existing = await self._existing_columns(db, table)

            # 1) Backfill da coluna `_raw` (tabelas criadas antes desta correção).
            if _RAW_COL not in existing:
                try:
                    await db.execute(
                        f"ALTER TABLE {quote_ident(table)} "
                        f"ADD COLUMN IF NOT EXISTS {quote_ident(_RAW_COL)} JSONB"
                    )
                except Exception:
                    pass

            # 2) Auto-cura: alarga colunas de dado ainda tipadas para TEXT. Isso
            #    conserta tabelas legadas cujas colunas foram congeladas como
            #    BIGINT/DOUBLE/BOOLEAN pelo primeiro valor e agora recebem um
            #    tipo Python diferente.
            for col, dtype in existing.items():
                if col in _RESERVED_COLS:
                    continue
                if dtype not in ("text", "character varying", "character"):
                    try:
                        await db.execute(
                            f"ALTER TABLE {quote_ident(table)} "
                            f"ALTER COLUMN {quote_ident(col)} TYPE TEXT "
                            f"USING {quote_ident(col)}::text"
                        )
                    except Exception:
                        pass

            # 3) Adiciona colunas TEXT novas idempotentemente.
            for k in sample_data:
                if k in _RESERVED_COLS or k in existing:
                    continue
                try:
                    await db.execute(
                        f"ALTER TABLE {quote_ident(table)} "
                        f"ADD COLUMN IF NOT EXISTS {quote_ident(k)} TEXT"
                    )
                except Exception:
                    pass

    async def insert(
        self, table: str, data: dict[str, Any],
        user_id: str | None, username: str | None,
        case_number: str | None, transaction_id: str | None,
        feature: str | None,
    ) -> str:
        """Insere uma linha. Achata `data`, garante schema, serializa cada valor
        para TEXT e guarda o payload original em `_raw` (JSONB)."""
        if not is_safe_ident(table):
            raise ValueError(f"nome de tabela inválido: {table}")
        flat = _flatten(data)
        await self.ensure_table(table, flat)

        row_id = uuid.uuid4().hex
        full: dict[str, Any] = {
            "_id": row_id,
            "_user_id": user_id,
            "_username": username,
            "_case_number": case_number,
            "_transaction_id": transaction_id,
            "_feature": feature,
            # `_raw`: payload estruturado original (dict/list) — asyncpg encoda
            # via codec JSONB registrado no pool. Fidelidade total, à prova de
            # type-drift e de colisão de nomes por truncamento.
            _RAW_COL: data,
        }
        for k, v in flat.items():
            # Colunas de dado são TEXT → serializa todo valor para string estável.
            full[k] = _to_text(v)

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
                "WHERE table_schema = current_schema() AND table_name = $1 "
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
