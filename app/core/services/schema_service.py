"""Use case: introspecção do schema PostgreSQL — devolve tabelas, colunas e
amostras para a UI poder oferecer qualquer coluna como input para um módulo.
"""

from __future__ import annotations

from app.adapters.db.postgres import connect, is_safe_ident, quote_ident


# Tabelas escondidas — não fazem sentido como input de módulos
_HIDDEN_TABLES = {
    "user_roles", "role_permissions",  # tabelas de relacionamento N:N
}

# Colunas escondidas em todas as tabelas (PII, IDs internos, payloads opacos)
_HIDDEN_COLUMNS = {
    "hashed_password", "salt", "password",
    "raw_json",         # transcripts.raw_json é gigante e duplica os outros campos
}

# Tabelas que devem aparecer com label amigável + escopo de funcionalidade
# (feature → ['radar', 'churn', ...]) define em qual tela a tabela aparece no picker
_TABLE_META = {
    "bko_cases":             {"label": "Casos BKO (XLSX)",      "features": ["radar"]},
    "transcripts":           {"label": "Transcrições (JSON)",   "features": ["radar"]},
    "contracts":             {"label": "Contratos (legado)",    "features": ["radar"]},
    "analysis_cards":        {"label": "Cards de análise",      "features": ["radar"]},
    "churn_nodes":           {"label": "Taxonomia churn",       "features": ["churn"]},
    "churn_classifications": {"label": "Classificações churn",  "features": ["churn"]},
    "modules":               {"label": "Módulos",               "features": ["admin"]},
    "prompts":               {"label": "Prompts",               "features": ["admin"]},
    "users":                 {"label": "Usuários",              "features": ["admin"]},
    "finops_ledger":         {"label": "FinOps (ledger)",       "features": ["admin"]},
    "failsafe_actions":      {"label": "Failsafe (ações)",      "features": ["admin"]},
    "roles":                 {"label": "Papéis",                "features": ["admin"]},
    "permissions":           {"label": "Permissões",            "features": ["admin"]},
}


# Colunas/tipos no PostgreSQL para os quais agregação numérica faz sentido.
# O legado SQLite usava CAST(... AS REAL) cego — em PG isso falha em colunas
# JSONB e BOOLEAN. Aqui detectamos numéricos antes de gerar CAST.
_NUMERIC_PG_TYPES = {
    "smallint", "integer", "bigint",
    "decimal", "numeric",
    "real", "double precision",
    "smallserial", "serial", "bigserial",
}


class SchemaService:
    """Lê metadados via information_schema + amostra valores reais para preview."""

    async def list_tables(self, feature: str | None = None) -> list[dict]:
        """Devolve tabelas com colunas + samples. Se `feature` informada,
        filtra por escopo."""
        async with connect() as db:
            tables_rows = await db.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            )
            table_names = [r["table_name"] for r in tables_rows]

            tables: list[dict] = []
            for tname in table_names:
                if tname in _HIDDEN_TABLES:
                    continue
                meta = _TABLE_META.get(tname)
                if meta is None:
                    # tabela dinâmica criada por módulo response_type='table'
                    if "__" in tname:
                        module_part, _, feature_part = tname.rpartition("__")
                        if module_part and feature_part:
                            meta = {
                                "label": f"{module_part} (gerada por módulo)",
                                "features": [feature_part],
                                "is_dynamic": True,
                            }
                if meta is None:
                    continue
                if feature and feature not in meta["features"]:
                    continue

                row_count = await db.fetchval(
                    f"SELECT COUNT(*) FROM {quote_ident(tname)}"
                )
                row_count = int(row_count or 0)

                col_rows = await db.fetch(
                    "SELECT column_name, data_type, ordinal_position "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = $1 "
                    "ORDER BY ordinal_position",
                    tname,
                )
                pk_rows = await db.fetch(
                    "SELECT a.attname AS column_name "
                    "FROM   pg_index i "
                    "JOIN   pg_attribute a "
                    "       ON a.attrelid = i.indrelid "
                    "      AND a.attnum  = ANY(i.indkey) "
                    "WHERE  i.indrelid = $1::regclass AND i.indisprimary",
                    f"public.{tname}",
                )
                pk_cols = {r["column_name"] for r in pk_rows}

                columns: list[dict] = []
                for col in col_rows:
                    cname = col["column_name"]
                    if cname in _HIDDEN_COLUMNS:
                        continue
                    ctype = (col["data_type"] or "").upper() or "TEXT"
                    is_pk = cname in pk_cols

                    samples: list[str] = []
                    non_null = 0
                    if row_count > 0:
                        try:
                            non_null = await db.fetchval(
                                f"SELECT COUNT(*) FROM {quote_ident(tname)} "
                                f"WHERE {quote_ident(cname)} IS NOT NULL "
                                f"  AND {quote_ident(cname)}::text <> ''"
                            )
                            non_null = int(non_null or 0)

                            sample_rows = await db.fetch(
                                f"SELECT DISTINCT {quote_ident(cname)} AS v "
                                f"FROM {quote_ident(tname)} "
                                f"WHERE {quote_ident(cname)} IS NOT NULL "
                                f"  AND {quote_ident(cname)}::text <> '' "
                                "LIMIT 3"
                            )
                            for vrow in sample_rows:
                                v = vrow["v"]
                                if v is None:
                                    continue
                                s = str(v)
                                if len(s) > 80:
                                    s = s[:80] + "…"
                                samples.append(s)
                        except Exception:
                            pass

                    columns.append({
                        "name": cname,
                        "type": ctype,
                        "is_pk": is_pk,
                        "non_null_count": non_null,
                        "sample_values": samples,
                    })

                tables.append({
                    "name": tname,
                    "label": meta["label"],
                    "features": meta["features"],
                    "row_count": row_count,
                    "columns": columns,
                    "is_dynamic": meta.get("is_dynamic", False),
                })
            return tables

    async def fetch_column_values(
        self,
        table: str,
        column: str,
        limit: int = 50,
    ) -> list[str]:
        """Lista valores distintos não-nulos de uma coluna."""
        if not is_safe_ident(table) or not is_safe_ident(column):
            raise ValueError("nome de tabela/coluna inválido")
        if table in _HIDDEN_TABLES or column in _HIDDEN_COLUMNS:
            raise ValueError("tabela/coluna não acessível")

        async with connect() as db:
            rows = await db.fetch(
                f"SELECT DISTINCT {quote_ident(column)} AS v "
                f"FROM {quote_ident(table)} "
                f"WHERE {quote_ident(column)} IS NOT NULL "
                f"  AND {quote_ident(column)}::text <> '' "
                "LIMIT $1",
                min(limit, 500),
            )
            return [str(r["v"]) for r in rows if r["v"] is not None]

    async def get_value(self, table: str, column: str, pk_column: str, pk_value: str) -> str | None:
        """Recupera o valor de uma coluna específica para uma linha
        identificada pela PK."""
        if not all(is_safe_ident(x) for x in (table, column, pk_column)):
            raise ValueError("identificador inválido")
        if table in _HIDDEN_TABLES or column in _HIDDEN_COLUMNS:
            raise ValueError("tabela/coluna não acessível")
        async with connect() as db:
            row = await db.fetchrow(
                f"SELECT {quote_ident(column)} AS v FROM {quote_ident(table)} "
                f"WHERE {quote_ident(pk_column)}::text = $1 LIMIT 1",
                pk_value,
            )
            return None if not row or row["v"] is None else str(row["v"])

    async def fetch_series(
        self,
        table: str,
        label_column: str,
        value_column: str,
        aggregate: str = "sum",
        order_by: str = "label_asc",
        limit: int = 50,
        filters: list[dict] | None = None,
        value_expr: str = "",
    ) -> dict:
        """Devolve {labels, values, agg, total_rows} para alimentar Chart.js."""
        if not is_safe_ident(table) or not is_safe_ident(label_column):
            raise ValueError("identificador inválido")
        if value_column and not is_safe_ident(value_column):
            raise ValueError("identificador de value_column inválido")
        if table in _HIDDEN_TABLES:
            raise ValueError("tabela não acessível")
        if label_column in _HIDDEN_COLUMNS or (value_column and value_column in _HIDDEN_COLUMNS):
            raise ValueError("coluna não acessível")
        agg = aggregate.lower()
        if agg not in {"sum", "count", "avg", "min", "max", "none"}:
            raise ValueError(f"aggregate inválido: {aggregate}")

        raw_value_expr = (value_expr or "").strip()
        if raw_value_expr:
            await _validate_value_expr(raw_value_expr, table)

        if agg == "count":
            sql_value_expr = "COUNT(*)"
        elif agg == "none":
            if raw_value_expr:
                sql_value_expr = raw_value_expr
            else:
                sql_value_expr = quote_ident(value_column) if value_column else "1"
        else:
            if raw_value_expr:
                sql_value_expr = f"{agg.upper()}(CAST(({raw_value_expr}) AS DOUBLE PRECISION))"
            elif value_column:
                sql_value_expr = (
                    f"{agg.upper()}(CAST({quote_ident(value_column)} "
                    f"AS DOUBLE PRECISION))"
                )
            else:
                raise ValueError(f"aggregate={agg} requer value_column ou value_expr")

        order_clauses = {
            "label_asc":  '"_label" ASC',
            "label_desc": '"_label" DESC',
            "value_desc": '"_value" DESC NULLS LAST',
            "value_asc":  '"_value" ASC NULLS LAST',
        }
        order_clause = order_clauses.get(order_by, '"_label" ASC')

        # Filtros adicionais (crossfilter / globais) — só op '=' nesta fase.
        extra_where = ""
        extra_params: list = []
        for f in (filters or []):
            col = f.get("column")
            op = f.get("op", "=")
            val = f.get("value")
            if not col or not is_safe_ident(col) or col in _HIDDEN_COLUMNS:
                raise ValueError(f"filtro com coluna inválida: {col}")
            if op != "=":
                raise ValueError(f"operador de filtro não suportado: {op}")
            extra_params.append(val)
            extra_where += f" AND {quote_ident(col)} = ${len(extra_params)}"

        limit_param = len(extra_params) + 1

        async with connect() as db:
            if agg == "none":
                sql = (
                    f'SELECT {quote_ident(label_column)} AS "_label", '
                    f'       {sql_value_expr} AS "_value" '
                    f'FROM {quote_ident(table)} '
                    f'WHERE {quote_ident(label_column)} IS NOT NULL{extra_where} '
                    f'ORDER BY {order_clause} LIMIT ${limit_param}'
                )
            else:
                sql = (
                    f'SELECT {quote_ident(label_column)} AS "_label", '
                    f'       {sql_value_expr} AS "_value" '
                    f'FROM {quote_ident(table)} '
                    f'WHERE {quote_ident(label_column)} IS NOT NULL{extra_where} '
                    f'GROUP BY {quote_ident(label_column)} '
                    f'ORDER BY {order_clause} LIMIT ${limit_param}'
                )
            try:
                rows = await db.fetch(sql, *extra_params, limit)
            except Exception as e:
                raise ValueError(f"erro na consulta: {e}")

            labels: list[str] = []
            values: list[float] = []
            for r in rows:
                lbl = r["_label"]
                val = r["_value"]
                if lbl is None or val is None:
                    continue
                labels.append(str(lbl))
                try:
                    values.append(float(val))
                except (TypeError, ValueError):
                    values.append(0.0)

            total = await db.fetchval(
                f"SELECT COUNT(*) FROM {quote_ident(table)}"
            )
            total = int(total or 0)

        return {
            "labels": labels,
            "values": values,
            "aggregate": agg,
            "label_column": label_column,
            "value_column": value_column or "(count)",
            "total_rows": total,
            "rows_returned": len(labels),
        }


# Whitelist de palavras-chave permitidas em variáveis calculadas.
# `REAL` mantido por compat — na geração SQL usamos DOUBLE PRECISION.
_EXPR_KEYWORDS = {"CAST", "AS", "REAL", "DOUBLE", "PRECISION", "INTEGER", "TEXT",
                  "ROUND", "ABS", "COALESCE"}
import re as _re  # local alias

_EXPR_TOKEN_RE = _re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_EXPR_ALLOWED_CHARS = _re.compile(r"^[A-Za-z0-9_+\-*/().,\s]+$")


async def _validate_value_expr(expr: str, table: str) -> None:
    """Valida expressão (variável calculada) contra:

       - whitelist de caracteres
       - tamanho máximo (200 chars)
       - identificadores devem ser palavras-chave permitidas OU colunas reais
         da tabela

    Levanta ValueError se inválido. Não executa SQL fora da introspecção."""
    if len(expr) > 200:
        raise ValueError("expressão muito longa (máx 200)")
    if not _EXPR_ALLOWED_CHARS.match(expr):
        raise ValueError("expressão contém caracteres não permitidos")
    if not is_safe_ident(table):
        raise ValueError("nome de tabela inválido")
    async with connect() as db:
        rows = await db.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1",
            table,
        )
        cols = {r["column_name"] for r in rows}
    for tok in _EXPR_TOKEN_RE.findall(expr):
        if tok.upper() in _EXPR_KEYWORDS:
            continue
        if tok in cols and tok not in _HIDDEN_COLUMNS:
            continue
        raise ValueError(f"identificador '{tok}' não é coluna da tabela ou função permitida")


# Manter alias `_is_safe_ident` do módulo legado pra que o código que
# importa do schema_service continue compilando (raiox_service usa).
_is_safe_ident = is_safe_ident
