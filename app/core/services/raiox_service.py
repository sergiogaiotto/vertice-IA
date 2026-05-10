"""Use case: Raio X Cliente.

Orquestra boards (pranchetas), charts (tiles Plotly) e relacionamentos entre
tabelas. Reaproveita SchemaService para introspecção e fetch_series para
queries 1D agregadas. Para Fase 0, queries com join são abstraídas via
`build_series` que delega ao SchemaService quando há uma única tabela.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.domain.entities import (
    RaioXBoard,
    RaioXChart,
    RaioXRelationship,
    new_uuid,
)
from app.core.ports.repositories import (
    RaioXBoardRepository,
    RaioXChartRepository,
    RaioXRelationshipRepository,
)
from app.adapters.db.postgres import connect
from app.core.services.schema_service import SchemaService, _is_safe_ident


# ---- catálogo válido de chart types ----
# Mantido o alias _F0 para compat dos testes; SUPPORTED_CHART_TYPES é a fonte da verdade.
SUPPORTED_CHART_TYPES = {
    # F0
    "bar", "line", "scatter", "pie", "histogram", "box",
    # F1
    "donut", "treemap", "sunburst", "funnel", "violin", "area",
    "heatmap", "waterfall", "indicator",
}
SUPPORTED_CHART_TYPES_F0 = SUPPORTED_CHART_TYPES


class RaioXService:
    def __init__(
        self,
        boards: RaioXBoardRepository,
        charts: RaioXChartRepository,
        rels: RaioXRelationshipRepository,
        schema: SchemaService,
    ):
        self._boards = boards
        self._charts = charts
        self._rels = rels
        self._schema = schema

    # ---------------- Boards ----------------

    async def list_boards(
        self,
        user_id: str | None,
        user_roles: list[str] | None = None,
        user_department: str | None = None,
    ) -> list[RaioXBoard]:
        """Devolve boards visíveis ao usuário.

        Visibilidade (OR entre todas):
          - is_shared=True
          - owner_id == user_id
          - alguma role do usuário está em allowed_roles
          - department do usuário está em allowed_departments
        """
        all_visible = await self._boards.list_visible(user_id)
        roles_set = set(user_roles or [])
        return [
            b for b in all_visible
            if (
                b.is_shared
                or (user_id and b.owner_id == user_id)
                or (b.allowed_roles and roles_set & set(b.allowed_roles))
                or (b.allowed_departments and user_department and user_department in b.allowed_departments)
            )
        ]

    async def get_board(self, board_id: UUID) -> RaioXBoard | None:
        return await self._boards.get(board_id)

    async def create_board(
        self,
        name: str,
        owner_id: str | None,
        description: str = "",
        is_shared: bool = True,
        cover_emoji: str = "🩻",
        allowed_roles: list[str] | None = None,
        allowed_departments: list[str] | None = None,
    ) -> RaioXBoard:
        # Se houver restrição por papel ou dept, força is_shared=False (caso
        # contrário a restrição seria ignorada — board público vence).
        roles = [r.strip() for r in (allowed_roles or []) if r and r.strip()]
        depts = [d.strip() for d in (allowed_departments or []) if d and d.strip()]
        effective_shared = is_shared and not (roles or depts)
        board = RaioXBoard(
            id=new_uuid(),
            name=name.strip() or "Sem nome",
            description=description,
            owner_id=owner_id,
            is_shared=effective_shared,
            allowed_roles=roles,
            allowed_departments=depts,
            layout={"cols": 3, "rows": 10},
            filters={},
            cover_emoji=cover_emoji,
        )
        return await self._boards.save(board)

    async def update_board(
        self,
        board_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        layout: dict[str, Any] | None = None,
        filters: dict[str, Any] | None = None,
        is_shared: bool | None = None,
        allowed_roles: list[str] | None = None,
        allowed_departments: list[str] | None = None,
    ) -> RaioXBoard | None:
        board = await self._boards.get(board_id)
        if not board:
            return None
        if name is not None:
            board.name = name
        if description is not None:
            board.description = description
        if layout is not None:
            board.layout = layout
        if filters is not None:
            board.filters = filters
        if is_shared is not None:
            board.is_shared = is_shared
        if allowed_roles is not None:
            board.allowed_roles = [r.strip() for r in allowed_roles if r and r.strip()]
        if allowed_departments is not None:
            board.allowed_departments = [d.strip() for d in allowed_departments if d and d.strip()]
        # Coerência: se há restrição por papel/dept, board não pode ser shared
        if board.allowed_roles or board.allowed_departments:
            board.is_shared = False
        board.updated_at = datetime.utcnow()
        return await self._boards.save(board)

    async def delete_board(self, board_id: UUID) -> bool:
        return await self._boards.delete(board_id)

    # ---------------- Charts ----------------

    async def list_charts(self, board_id: UUID) -> list[RaioXChart]:
        return await self._charts.list_for_board(board_id)

    async def add_chart(
        self,
        board_id: UUID,
        chart_type: str,
        query_spec: dict[str, Any],
        title: str = "",
        position_row: int = 0,
        position_col: int = 0,
        span_cols: int = 1,
        span_rows: int = 1,
        plotly_config: dict[str, Any] | None = None,
        skill_path: str = "",
        created_by_ai: bool = False,
    ) -> RaioXChart:
        if chart_type not in SUPPORTED_CHART_TYPES:
            raise ValueError(
                f"chart_type '{chart_type}' não suportado. "
                f"Aceitos: {sorted(SUPPORTED_CHART_TYPES)}"
            )
        chart = RaioXChart(
            id=new_uuid(),
            board_id=board_id,
            title=title,
            chart_type=chart_type,
            position_row=max(0, min(int(position_row), 9)),
            position_col=max(0, min(int(position_col), 2)),
            span_cols=max(1, min(int(span_cols), 3)),
            span_rows=max(1, min(int(span_rows), 2)),
            query_spec=query_spec,
            plotly_config=plotly_config or {},
            skill_path=skill_path or "",
            created_by_ai=created_by_ai,
        )
        return await self._charts.save(chart)

    async def update_chart(
        self,
        chart_id: UUID,
        **fields: Any,
    ) -> RaioXChart | None:
        chart = await self._charts.get(chart_id)
        if not chart:
            return None
        for k, v in fields.items():
            if v is None:
                continue
            if k == "chart_type" and v not in SUPPORTED_CHART_TYPES:
                raise ValueError(f"chart_type '{v}' não suportado")
            if hasattr(chart, k):
                setattr(chart, k, v)
        chart.updated_at = datetime.utcnow()
        return await self._charts.save(chart)

    async def delete_chart(self, chart_id: UUID) -> bool:
        return await self._charts.delete(chart_id)

    # ---------------- Query (delegação ao SchemaService) ----------------

    async def build_series(self, query_spec: dict[str, Any]) -> dict[str, Any]:
        """Resolve uma query_spec em {labels, values}.

        Sem joins → delega ao SchemaService.fetch_series (1 tabela).
        Com joins → constrói SQL com INNER JOIN; cada join precisa estar
        registrado em raiox_relationships (whitelist), com identificadores
        validados e valores de filtro parametrizados.
        """
        table = query_spec.get("table")
        label_col = query_spec.get("label_column")
        value_col = query_spec.get("value_column", "")
        aggregate = query_spec.get("aggregate", "count")
        order_by = query_spec.get("order_by", "value_desc")
        limit = int(query_spec.get("limit", 30))
        joins = query_spec.get("joins") or []
        filters = query_spec.get("filters") or []

        if not table or not label_col:
            raise ValueError("query_spec exige 'table' e 'label_column'")

        if not joins:
            return await self._schema.fetch_series(
                table=table,
                label_column=label_col,
                value_column=value_col,
                aggregate=aggregate,
                order_by=order_by,
                limit=min(limit, 200),
                filters=filters,
                value_expr=query_spec.get("value_expr", ""),
            )

        return await self._build_series_with_joins(
            base_table=table,
            label_column=label_col,
            value_column=value_col,
            aggregate=aggregate,
            order_by=order_by,
            limit=min(limit, 200),
            filters=filters,
            joins=joins,
        )

    async def _build_series_with_joins(
        self,
        base_table: str,
        label_column: str,
        value_column: str,
        aggregate: str,
        order_by: str,
        limit: int,
        filters: list[dict],
        joins: list[dict],
    ) -> dict[str, Any]:
        # Validação de identificadores (defesa contra SQL injection).
        # label_column/value_column podem ser "coluna" ou "tabela.coluna" — partes
        # validadas individualmente.
        def _valid_qualified(expr: str) -> bool:
            if "." in expr:
                t, c = expr.split(".", 1)
                return _is_safe_ident(t) and _is_safe_ident(c)
            return _is_safe_ident(expr)

        if not _is_safe_ident(base_table) or not _valid_qualified(label_column):
            raise ValueError("identificador inválido em base/label")
        if value_column and not _valid_qualified(value_column):
            raise ValueError("identificador inválido em value_column")
        agg = aggregate.lower()
        if agg not in {"sum", "count", "avg", "min", "max"}:
            raise ValueError(f"aggregate '{aggregate}' não suportado em joins")

        # Whitelist: cada join deve corresponder a um par registrado em raiox_relationships
        # (em qualquer direção). Carrega tudo uma vez.
        existing = await self._rels.list_all()
        rel_set = {(r.table_a, r.column_a, r.table_b, r.column_b) for r in existing} | {
            (r.table_b, r.column_b, r.table_a, r.column_a) for r in existing
        }

        from_clause = f'"{base_table}" AS t0'
        aliases = {base_table: "t0"}
        for i, j in enumerate(joins, start=1):
            from_t = j.get("from_table")
            from_c = j.get("from_column")
            to_t = j.get("to_table")
            to_c = j.get("to_column")
            if not all([from_t, from_c, to_t, to_c]):
                raise ValueError("join exige from_table/from_column/to_table/to_column")
            for ident in (from_t, from_c, to_t, to_c):
                if not _is_safe_ident(ident):
                    raise ValueError(f"identificador inválido em join: {ident}")
            if (from_t, from_c, to_t, to_c) not in rel_set:
                raise ValueError(
                    f"join {from_t}.{from_c} ↔ {to_t}.{to_c} não está em raiox_relationships"
                )
            if from_t not in aliases:
                raise ValueError(f"from_table '{from_t}' precisa estar na FROM antes de joinar")
            alias = f"t{i}"
            aliases[to_t] = alias
            from_clause += (
                f' INNER JOIN "{to_t}" AS {alias} ON '
                f'{aliases[from_t]}."{from_c}" = {alias}."{to_c}"'
            )

        # Resolve label/value column para um alias se prefixado com "tabela."
        def _qual(col_expr: str) -> str:
            if "." in col_expr:
                t, c = col_expr.split(".", 1)
                if t not in aliases:
                    raise ValueError(f"coluna '{col_expr}' aponta para tabela fora do FROM")
                if not _is_safe_ident(t) or not _is_safe_ident(c):
                    raise ValueError(f"identificador inválido: {col_expr}")
                return f'{aliases[t]}."{c}"'
            return f'"{col_expr}"'

        label_expr = _qual(label_column)
        if agg == "count":
            value_expr = "COUNT(*)"
        else:
            if not value_column:
                raise ValueError(f"aggregate={agg} requer value_column")
            value_expr = f'{agg.upper()}(CAST({_qual(value_column)} AS DOUBLE PRECISION))'

        order_clauses = {
            "label_asc":  '"_label" ASC',
            "label_desc": '"_label" DESC',
            "value_desc": '"_value" DESC NULLS LAST',
            "value_asc":  '"_value" ASC NULLS LAST',
        }
        order_clause = order_clauses.get(order_by, '"_label" ASC')

        # Filtros (op = '=' apenas, valores parametrizados via $N do asyncpg).
        where = f'{label_expr} IS NOT NULL'
        params: list = []
        for f in filters:
            col = f.get("column")
            op = f.get("op", "=")
            val = f.get("value")
            if not col:
                continue
            if op != "=":
                raise ValueError(f"operador '{op}' não suportado")
            params.append(val)
            where += f' AND {_qual(col)} = ${len(params)}'

        params.append(limit)
        sql = (
            f'SELECT {label_expr} AS "_label", {value_expr} AS "_value" '
            f'FROM {from_clause} '
            f'WHERE {where} '
            f'GROUP BY {label_expr} '
            f'ORDER BY {order_clause} LIMIT ${len(params)}'
        )
        async with connect() as db:
            try:
                rows = await db.fetch(sql, *params)
            except Exception as e:
                raise ValueError(f"erro na consulta com joins: {e}")
            total = await db.fetchval(f'SELECT COUNT(*) FROM "{base_table}"')
            total = int(total or 0)

        labels: list[str] = []
        values: list[float] = []
        for r in rows:
            if r["_label"] is None or r["_value"] is None:
                continue
            labels.append(str(r["_label"]))
            try:
                values.append(float(r["_value"]))
            except (TypeError, ValueError):
                values.append(0.0)
        return {
            "labels": labels,
            "values": values,
            "aggregate": agg,
            "label_column": label_column,
            "value_column": value_column or "(count)",
            "total_rows": total,
            "rows_returned": len(labels),
        }

    # ---------------- Relationships ----------------

    async def list_relationships(self) -> list[RaioXRelationship]:
        return await self._rels.list_all()

    async def detect_relationships(
        self,
        only_unconfirmed: bool = True,
    ) -> list[RaioXRelationship]:
        """Heurística simples (Fase 0): para cada par de tabelas visíveis,
        se ambas tiverem coluna com o mesmo nome E uma das duas é PK, propõe
        relação 1:N com confidence proporcional à raridade do nome.

        Não persiste — só devolve sugestões. UI ou Fase 2 decidem persistir.
        """
        tables = await self._schema.list_tables()
        # mapa: nome de coluna → [(table, is_pk), ...]
        col_index: dict[str, list[tuple[str, bool]]] = {}
        for t in tables:
            for c in t["columns"]:
                col_index.setdefault(c["name"], []).append((t["name"], c["is_pk"]))

        existing = {
            (r.table_a, r.column_a, r.table_b, r.column_b)
            for r in await self._rels.list_all()
        }

        suggestions: list[RaioXRelationship] = []
        for col_name, owners in col_index.items():
            if len(owners) < 2:
                continue
            pks = [o for o in owners if o[1]]
            non_pks = [o for o in owners if not o[1]]
            if not pks:
                continue
            for pk_table, _ in pks:
                for fk_table, _ in non_pks:
                    if pk_table == fk_table:
                        continue
                    key = (pk_table, col_name, fk_table, col_name)
                    if only_unconfirmed and key in existing:
                        continue
                    confidence = round(min(0.9, 0.5 + 0.1 / len(owners)), 2)
                    suggestions.append(
                        RaioXRelationship(
                            id=new_uuid(),
                            table_a=pk_table,
                            column_a=col_name,
                            table_b=fk_table,
                            column_b=col_name,
                            kind="one_to_many",
                            confidence=confidence,
                        )
                    )
        return suggestions

    async def save_relationship(self, rel: RaioXRelationship) -> RaioXRelationship:
        return await self._rels.save(rel)

    async def confirm_relationship(self, rel_id: UUID, username: str) -> bool:
        return await self._rels.confirm(rel_id, username)

    async def delete_relationship(self, rel_id: UUID) -> bool:
        return await self._rels.delete(rel_id)
