"""Use case: introspecção do schema SQLite — devolve tabelas, colunas e amostras
para a UI poder oferecer qualquer coluna como input para um módulo.
"""

from __future__ import annotations

from app.adapters.db.sqlite import connect


# Tabelas escondidas — não fazem sentido como input de módulos
_HIDDEN_TABLES = {
    "sqlite_sequence",
    "sqlite_stat1",
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


class SchemaService:
    """Lê metadados via PRAGMA + amostra valores reais para preview."""

    async def list_tables(self, feature: str | None = None) -> list[dict]:
        """Devolve tabelas com colunas + samples. Se `feature` informada, filtra por escopo."""
        async with connect() as db:
            cur = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            table_names = [r[0] for r in await cur.fetchall()]

            tables: list[dict] = []
            for tname in table_names:
                if tname in _HIDDEN_TABLES:
                    continue
                meta = _TABLE_META.get(tname)
                if meta is None:
                    # tabela dinâmica criada por módulo response_type='table'
                    # convenção: nome = "{module_name}__{feature}"
                    # (ex: extracao_dados_texto_livre__radar)
                    if "__" in tname:
                        module_part, _, feature_part = tname.rpartition("__")
                        if module_part and feature_part:
                            meta = {
                                "label": f"{module_part} (gerada por módulo)",
                                "features": [feature_part],
                                "is_dynamic": True,
                            }
                if meta is None:
                    # tabela desconhecida e não-dinâmica — esconder por segurança
                    continue
                if feature and feature not in meta["features"]:
                    continue

                cur = await db.execute(f'SELECT COUNT(*) FROM "{tname}"')
                row_count = int((await cur.fetchone())[0])

                cur = await db.execute(f'PRAGMA table_info("{tname}")')
                col_rows = await cur.fetchall()

                columns: list[dict] = []
                for col in col_rows:
                    cname = col[1]
                    if cname in _HIDDEN_COLUMNS:
                        continue
                    ctype = (col[2] or "").upper() or "TEXT"
                    is_pk = bool(col[5])

                    samples: list[str] = []
                    non_null = 0
                    if row_count > 0:
                        try:
                            cur = await db.execute(
                                f'SELECT COUNT(*) FROM "{tname}" '
                                f'WHERE "{cname}" IS NOT NULL AND "{cname}" != \'\''
                            )
                            non_null = int((await cur.fetchone())[0])

                            cur = await db.execute(
                                f'SELECT DISTINCT "{cname}" FROM "{tname}" '
                                f'WHERE "{cname}" IS NOT NULL AND "{cname}" != \'\' '
                                f'LIMIT 3'
                            )
                            for vrow in await cur.fetchall():
                                v = vrow[0]
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
        """Lista valores distintos não-nulos de uma coluna (para popular dropdown)."""
        # validação anti-injection: nomes precisam ser identificadores SQL válidos
        if not _is_safe_ident(table) or not _is_safe_ident(column):
            raise ValueError("nome de tabela/coluna inválido")
        if table in _HIDDEN_TABLES or column in _HIDDEN_COLUMNS:
            raise ValueError("tabela/coluna não acessível")

        async with connect() as db:
            cur = await db.execute(
                f"SELECT DISTINCT \"{column}\" FROM \"{table}\" "
                f"WHERE \"{column}\" IS NOT NULL AND \"{column}\" != '' "
                f"LIMIT ?",
                (min(limit, 500),),
            )
            return [str(r[0]) for r in await cur.fetchall() if r[0] is not None]

    async def get_value(self, table: str, column: str, pk_column: str, pk_value: str) -> str | None:
        """Recupera o valor de uma coluna específica para uma linha identificada pela PK."""
        if not all(_is_safe_ident(x) for x in (table, column, pk_column)):
            raise ValueError("identificador inválido")
        if table in _HIDDEN_TABLES or column in _HIDDEN_COLUMNS:
            raise ValueError("tabela/coluna não acessível")
        async with connect() as db:
            cur = await db.execute(
                f"SELECT \"{column}\" FROM \"{table}\" WHERE \"{pk_column}\" = ? LIMIT 1",
                (pk_value,),
            )
            row = await cur.fetchone()
            return None if not row or row[0] is None else str(row[0])

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
        """Devolve {labels, values, agg, total_rows} para alimentar Chart.js.

        - aggregate='count' devolve contagem por label (ignora value_column ou usa COUNT(*))
        - aggregate='sum'|'avg'|'min'|'max' agrega value_column GROUP BY label_column
        - aggregate='none' devolve linhas brutas (label, value) sem agregação
        - order_by: ordenação dos resultados
        - filters: lista de {column, op, value} aplicados como WHERE adicional.
                   ops aceitos: '='. Identificadores validados com _is_safe_ident,
                   valores parametrizados (sqlite-binding) — sem risco de injection.
        """
        if not _is_safe_ident(table) or not _is_safe_ident(label_column):
            raise ValueError("identificador inválido")
        if value_column and not _is_safe_ident(value_column):
            raise ValueError("identificador de value_column inválido")
        if table in _HIDDEN_TABLES:
            raise ValueError("tabela não acessível")
        if label_column in _HIDDEN_COLUMNS or (value_column and value_column in _HIDDEN_COLUMNS):
            raise ValueError("coluna não acessível")
        agg = aggregate.lower()
        if agg not in {"sum", "count", "avg", "min", "max", "none"}:
            raise ValueError(f"aggregate inválido: {aggregate}")

        # Variável calculada: expressão crua validada contra whitelist de colunas
        # da tabela + operadores aritméticos + funções permitidas.
        raw_value_expr = (value_expr or "").strip()
        if raw_value_expr:
            await _validate_value_expr(raw_value_expr, table)

        if agg == "count":
            sql_value_expr = "COUNT(*)"
        elif agg == "none":
            if raw_value_expr:
                sql_value_expr = raw_value_expr
            else:
                sql_value_expr = f'"{value_column}"' if value_column else "1"
        else:
            if raw_value_expr:
                sql_value_expr = f'{agg.upper()}(CAST(({raw_value_expr}) AS REAL))'
            elif value_column:
                sql_value_expr = f'{agg.upper()}(CAST("{value_column}" AS REAL))'
            else:
                raise ValueError(f"aggregate={agg} requer value_column ou value_expr")

        # ordenação
        order_clauses = {
            "label_asc":  '"_label" ASC',
            "label_desc": '"_label" DESC',
            "value_desc": '"_value" DESC',
            "value_asc":  '"_value" ASC',
        }
        order_clause = order_clauses.get(order_by, '"_label" ASC')

        # Filtros adicionais (crossfilter / globais) — só op '=' nesta fase.
        extra_where = ""
        extra_params: list = []
        if filters:
            for f in filters:
                col = f.get("column")
                op = f.get("op", "=")
                val = f.get("value")
                if not col or not _is_safe_ident(col) or col in _HIDDEN_COLUMNS:
                    raise ValueError(f"filtro com coluna inválida: {col}")
                if op != "=":
                    raise ValueError(f"operador de filtro não suportado: {op}")
                extra_where += f' AND "{col}" = ?'
                extra_params.append(val)

        async with connect() as db:
            if agg == "none":
                sql = (
                    f'SELECT "{label_column}" AS "_label", {sql_value_expr} AS "_value" '
                    f'FROM "{table}" '
                    f'WHERE "{label_column}" IS NOT NULL{extra_where} '
                    f'ORDER BY {order_clause} LIMIT ?'
                )
            else:
                sql = (
                    f'SELECT "{label_column}" AS "_label", {sql_value_expr} AS "_value" '
                    f'FROM "{table}" '
                    f'WHERE "{label_column}" IS NOT NULL{extra_where} '
                    f'GROUP BY "{label_column}" '
                    f'ORDER BY {order_clause} LIMIT ?'
                )
            try:
                cur = await db.execute(sql, (*extra_params, limit))
                rows = await cur.fetchall()
            except Exception as e:
                raise ValueError(f"erro na consulta: {e}")

            labels = []
            values = []
            for r in rows:
                lbl = r[0]
                val = r[1]
                if lbl is None or val is None:
                    continue
                labels.append(str(lbl))
                try:
                    values.append(float(val))
                except (TypeError, ValueError):
                    values.append(0.0)

            cur = await db.execute(f'SELECT COUNT(*) FROM "{table}"')
            total = int((await cur.fetchone())[0])

        return {
            "labels": labels,
            "values": values,
            "aggregate": agg,
            "label_column": label_column,
            "value_column": value_column or "(count)",
            "total_rows": total,
            "rows_returned": len(labels),
        }


def _is_safe_ident(name: str) -> bool:
    """Valida que `name` é um identificador SQL seguro (a-z, 0-9, _)."""
    if not name or len(name) > 64:
        return False
    return all(c.isalnum() or c == "_" for c in name)


# Whitelist de palavras-chave permitidas em variáveis calculadas
_EXPR_KEYWORDS = {"CAST", "AS", "REAL", "INTEGER", "TEXT", "ROUND", "ABS", "COALESCE"}
import re as _re  # local alias

_EXPR_TOKEN_RE = _re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_EXPR_ALLOWED_CHARS = _re.compile(r"^[A-Za-z0-9_+\-*/().,\s]+$")


async def _validate_value_expr(expr: str, table: str) -> None:
    """Valida `expr` (variável calculada) contra:
       - whitelist de caracteres (apenas alfanuméricos + + - * / ( ) , espaços)
       - tamanho máximo (200 chars)
       - identificadores devem ser palavras-chave permitidas OU colunas reais da tabela

    Levanta ValueError se inválido. Não executa SQL."""
    if len(expr) > 200:
        raise ValueError("expressão muito longa (máx 200)")
    if not _EXPR_ALLOWED_CHARS.match(expr):
        raise ValueError("expressão contém caracteres não permitidos")
    if not _is_safe_ident(table):
        raise ValueError("nome de tabela inválido")
    async with connect() as db:
        cur = await db.execute(f'PRAGMA table_info("{table}")')
        cols = {row[1] for row in await cur.fetchall()}
    for tok in _EXPR_TOKEN_RE.findall(expr):
        if tok.upper() in _EXPR_KEYWORDS:
            continue
        if tok in cols and tok not in _HIDDEN_COLUMNS:
            continue
        raise ValueError(f"identificador '{tok}' não é coluna da tabela ou função permitida")
