"""Repositório SQLite do FinOps Ledger + Orçamentos + Políticas."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from app.adapters.db.sqlite import connect
from app.core.domain.entities import (
    BudgetPeriod,
    BudgetScopeType,
    FinOpsAlert,
    FinOpsBudget,
    FinOpsEntry,
    FinOpsModelPolicy,
    RiskTier,
    ValueTier,
    new_uuid,
)
from app.core.ports.repositories import (
    FinOpsBudgetRepository,
    FinOpsModelPolicyRepository,
    FinOpsRepository,
)


# Whitelist de dimensões expostas para chargeback/showback. Foram definidas
# explicitamente para evitar SQL injection na composição dinâmica e para
# garantir que apenas colunas indexadas/seguras sejam agregadas.
_VALID_DIMENSIONS = {
    "module":      "COALESCE(SUBSTR(context_tag, 1, INSTR(context_tag, '/') - 1), context_tag)",
    "model":       "model_name",
    "domain":      "domain",
    "product":     "product",
    "agent":       "agent",
    "flow":        "flow",
    "prompt_id":   "prompt_id",
    "integration": "integration",
    "environment": "environment",
}


# Janela SQLite a partir do início do período corrente. Para mensal usamos o
# primeiro dia do mês atual; semanal a partir de segunda-feira; diária a
# partir de hoje meia-noite. Tudo em UTC (igual aos timestamps do ledger).
_PERIOD_START_SQL = {
    "daily":   "DATE('now')",
    "weekly":  "DATE('now', 'weekday 0', '-6 days')",  # segunda da semana atual
    "monthly": "DATE('now', 'start of month')",
}


def _row_to_budget(row) -> FinOpsBudget:
    return FinOpsBudget(
        id=UUID(row[0]),
        name=row[1],
        scope_type=BudgetScopeType(row[2]),
        scope_value=row[3],
        period=BudgetPeriod(row[4]),
        limit_brl=float(row[5] or 0.0),
        warning_threshold=float(row[6] or 0.8),
        hard_stop=bool(row[7]),
        notes=row[8],
        created_by=UUID(row[9]) if row[9] else None,
    )


def _row_to_policy(row) -> FinOpsModelPolicy:
    raw_features = row[6]
    features = None
    if raw_features:
        try:
            features = json.loads(raw_features)
        except (json.JSONDecodeError, TypeError):
            features = None
    return FinOpsModelPolicy(
        id=UUID(row[0]),
        model_name=row[1],
        risk_tier=RiskTier(row[2]),
        value_tier=ValueTier(row[3]),
        max_cost_per_call=float(row[4]) if row[4] is not None else None,
        max_tokens_per_call=int(row[5]) if row[5] is not None else None,
        allowed_features=features,
        rationale=row[7],
        enabled=bool(row[8]),
    )


def _row_to_alert(row) -> FinOpsAlert:
    return FinOpsAlert(
        id=UUID(row[0]),
        budget_id=UUID(row[1]),
        severity=row[2],
        cost_observed=float(row[3] or 0.0),
        limit_reference=float(row[4] or 0.0),
        period_start=datetime.fromisoformat(row[5]) if row[5] else None,
        period_end=datetime.fromisoformat(row[6]) if row[6] else None,
        triggered_at=datetime.fromisoformat(row[7]) if row[7] else datetime.utcnow(),
        resolved_at=datetime.fromisoformat(row[8]) if row[8] else None,
    )


class SqliteFinOpsRepository(FinOpsRepository):

    async def append(self, entry: FinOpsEntry) -> FinOpsEntry:
        async with connect() as db:
            cur = await db.execute(
                "INSERT INTO finops_ledger (user_id, module_id, model_name, tokens_input, "
                "tokens_output, cost_estimated, context_tag, domain, product, agent, flow, "
                "prompt_id, integration, environment, latency_ms, storage_bytes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(entry.user_id) if entry.user_id else None,
                    str(entry.module_id) if entry.module_id else None,
                    entry.model_name,
                    entry.tokens_input,
                    entry.tokens_output,
                    entry.cost_estimated,
                    entry.context_tag,
                    entry.domain,
                    entry.product,
                    entry.agent,
                    entry.flow,
                    entry.prompt_id,
                    entry.integration,
                    entry.environment or "production",
                    entry.latency_ms,
                    entry.storage_bytes,
                ),
            )
            entry.id = cur.lastrowid
            await db.commit()
            return entry

    async def aggregate_by_module(self) -> list[dict]:
        async with connect() as db:
            cur = await db.execute(
                "SELECT COALESCE(SUBSTR(context_tag, 1, INSTR(context_tag, '/') - 1), context_tag) AS module_name, "
                "  SUM(tokens_input) AS tokens_in, SUM(tokens_output) AS tokens_out, "
                "  SUM(cost_estimated) AS cost, COUNT(*) AS calls "
                "FROM finops_ledger GROUP BY module_name ORDER BY cost DESC"
            )
            return [
                {
                    "module": (r[0] or "outros"),
                    "tokens_input": int(r[1] or 0),
                    "tokens_output": int(r[2] or 0),
                    "cost": float(r[3] or 0),
                    "calls": int(r[4] or 0),
                }
                for r in await cur.fetchall()
            ]

    async def aggregate_by_model(self) -> list[dict]:
        async with connect() as db:
            cur = await db.execute(
                "SELECT model_name, SUM(tokens_input), SUM(tokens_output), SUM(cost_estimated), COUNT(*) "
                "FROM finops_ledger GROUP BY model_name ORDER BY SUM(cost_estimated) DESC"
            )
            return [
                {
                    "model": r[0] or "?",
                    "tokens_input": int(r[1] or 0),
                    "tokens_output": int(r[2] or 0),
                    "cost": float(r[3] or 0),
                    "calls": int(r[4] or 0),
                }
                for r in await cur.fetchall()
            ]

    async def session_total(self, session_id: str) -> float:
        # placeholder — sessões reais seriam tagueadas em context_tag
        async with connect() as db:
            cur = await db.execute(
                "SELECT SUM(cost_estimated) FROM finops_ledger WHERE context_tag LIKE ?",
                (f"%{session_id}%",),
            )
            row = await cur.fetchone()
            return float(row[0] or 0.0)

    async def aggregate_by_day(self, days: int = 7) -> list[dict]:
        """Agrega custo/calls por dia nos últimos N dias (mais recente primeiro)."""
        async with connect() as db:
            cur = await db.execute(
                "SELECT DATE(created_at) AS day, "
                "  SUM(cost_estimated) AS cost, "
                "  SUM(tokens_input + tokens_output) AS tokens, "
                "  COUNT(*) AS calls "
                "FROM finops_ledger "
                "WHERE created_at >= DATE('now', ?) "
                "GROUP BY day ORDER BY day DESC",
                (f"-{days} day",),
            )
            return [
                {
                    "day": r[0],
                    "cost": float(r[1] or 0),
                    "tokens": int(r[2] or 0),
                    "calls": int(r[3] or 0),
                }
                for r in await cur.fetchall()
            ]

    async def totals(self) -> dict:
        """Totais globais para os KPIs do Cockpit."""
        async with connect() as db:
            cur = await db.execute(
                "SELECT COUNT(*), SUM(cost_estimated), SUM(tokens_input + tokens_output) "
                "FROM finops_ledger"
            )
            r = await cur.fetchone()
            cur2 = await db.execute(
                "SELECT COUNT(*), SUM(cost_estimated) FROM finops_ledger "
                "WHERE created_at >= DATE('now', '-1 day')"
            )
            r24 = await cur2.fetchone()
            return {
                "calls_total": int(r[0] or 0),
                "cost_total": float(r[1] or 0),
                "tokens_total": int(r[2] or 0),
                "calls_24h": int(r24[0] or 0),
                "cost_24h": float(r24[1] or 0),
            }

    async def aggregate_by_dimension(self, dimension: str) -> list[dict]:
        """Agregação genérica para chargeback/showback. Whitelist em
        ``_VALID_DIMENSIONS`` evita injection na composição dinâmica."""
        expr = _VALID_DIMENSIONS.get(dimension)
        if not expr:
            raise ValueError(
                f"dimensão inválida: {dimension}. Use uma de {sorted(_VALID_DIMENSIONS)}"
            )
        async with connect() as db:
            cur = await db.execute(
                f"SELECT {expr} AS bucket, "
                "  SUM(tokens_input) AS tin, SUM(tokens_output) AS tout, "
                "  SUM(cost_estimated) AS cost, COUNT(*) AS calls, "
                "  AVG(latency_ms) AS avg_latency "
                "FROM finops_ledger "
                f"GROUP BY {expr} ORDER BY cost DESC"
            )
            return [
                {
                    "bucket": (r[0] or "(sem rateio)"),
                    "tokens_input": int(r[1] or 0),
                    "tokens_output": int(r[2] or 0),
                    "cost": float(r[3] or 0.0),
                    "calls": int(r[4] or 0),
                    "avg_latency_ms": float(r[5]) if r[5] is not None else None,
                }
                for r in await cur.fetchall()
            ]

    async def current_spend(
        self,
        scope_type: str,
        scope_value: str | None,
        period: str,
    ) -> float:
        """Soma de custo no período corrente para o escopo dado.

        Ex.: ``current_spend('module', 'radar', 'monthly')`` retorna o gasto do
        módulo Radar desde o primeiro dia do mês corrente.
        """
        period_start = _PERIOD_START_SQL.get(period)
        if not period_start:
            raise ValueError(
                f"período inválido: {period}. Use {sorted(_PERIOD_START_SQL)}"
            )
        # mapeia scope_type -> coluna no ledger. 'global' = sem WHERE de escopo.
        col_map = {
            "global":      None,
            "module":      "COALESCE(SUBSTR(context_tag, 1, INSTR(context_tag, '/') - 1), context_tag)",
            "model":       "model_name",
            "user":        "user_id",
            "domain":      "domain",
            "environment": "environment",
            "agent":       "agent",
        }
        if scope_type not in col_map:
            raise ValueError(f"scope_type inválido: {scope_type}")
        col = col_map[scope_type]
        params: list = []
        clause = f"created_at >= {period_start}"
        if col is not None and scope_value is not None:
            clause += f" AND {col} = ?"
            params.append(scope_value)
        async with connect() as db:
            cur = await db.execute(
                f"SELECT COALESCE(SUM(cost_estimated), 0) FROM finops_ledger WHERE {clause}",
                params,
            )
            row = await cur.fetchone()
            return float(row[0] or 0.0)


# ---------------------------------------------------------------------------
# Orçamentos
# ---------------------------------------------------------------------------


class SqliteFinOpsBudgetRepository(FinOpsBudgetRepository):

    _SELECT = (
        "SELECT id, name, scope_type, scope_value, period, limit_brl, "
        "warning_threshold, hard_stop, notes, created_by FROM finops_budgets"
    )

    async def list(self) -> list[FinOpsBudget]:
        async with connect() as db:
            cur = await db.execute(f"{self._SELECT} ORDER BY created_at DESC")
            return [_row_to_budget(r) for r in await cur.fetchall()]

    async def get(self, budget_id: UUID) -> FinOpsBudget | None:
        async with connect() as db:
            cur = await db.execute(f"{self._SELECT} WHERE id = ?", (str(budget_id),))
            row = await cur.fetchone()
            return _row_to_budget(row) if row else None

    async def save(self, budget: FinOpsBudget) -> FinOpsBudget:
        async with connect() as db:
            await db.execute(
                "INSERT INTO finops_budgets (id, name, scope_type, scope_value, period, "
                "limit_brl, warning_threshold, hard_stop, notes, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  name = excluded.name, "
                "  scope_type = excluded.scope_type, "
                "  scope_value = excluded.scope_value, "
                "  period = excluded.period, "
                "  limit_brl = excluded.limit_brl, "
                "  warning_threshold = excluded.warning_threshold, "
                "  hard_stop = excluded.hard_stop, "
                "  notes = excluded.notes, "
                "  updated_at = CURRENT_TIMESTAMP",
                (
                    str(budget.id),
                    budget.name,
                    budget.scope_type.value,
                    budget.scope_value,
                    budget.period.value,
                    budget.limit_brl,
                    budget.warning_threshold,
                    1 if budget.hard_stop else 0,
                    budget.notes,
                    str(budget.created_by) if budget.created_by else None,
                ),
            )
            await db.commit()
            return budget

    async def delete(self, budget_id: UUID) -> bool:
        async with connect() as db:
            cur = await db.execute(
                "DELETE FROM finops_budgets WHERE id = ?", (str(budget_id),)
            )
            await db.commit()
            return cur.rowcount > 0

    async def append_alert(self, alert: FinOpsAlert) -> FinOpsAlert:
        async with connect() as db:
            await db.execute(
                "INSERT INTO finops_alerts (id, budget_id, severity, cost_observed, "
                "limit_reference, period_start, period_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(alert.id),
                    str(alert.budget_id),
                    alert.severity,
                    alert.cost_observed,
                    alert.limit_reference,
                    alert.period_start.isoformat() if alert.period_start else None,
                    alert.period_end.isoformat() if alert.period_end else None,
                ),
            )
            await db.commit()
            return alert

    async def list_recent_alerts(self, limit: int = 20) -> list[FinOpsAlert]:
        async with connect() as db:
            cur = await db.execute(
                "SELECT id, budget_id, severity, cost_observed, limit_reference, "
                "period_start, period_end, triggered_at, resolved_at "
                "FROM finops_alerts ORDER BY triggered_at DESC LIMIT ?",
                (limit,),
            )
            return [_row_to_alert(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# Políticas de modelo
# ---------------------------------------------------------------------------


class SqliteFinOpsModelPolicyRepository(FinOpsModelPolicyRepository):

    _SELECT = (
        "SELECT id, model_name, risk_tier, value_tier, max_cost_per_call, "
        "max_tokens_per_call, allowed_features, rationale, enabled "
        "FROM finops_model_policies"
    )

    async def list(self) -> list[FinOpsModelPolicy]:
        async with connect() as db:
            cur = await db.execute(f"{self._SELECT} ORDER BY model_name ASC")
            return [_row_to_policy(r) for r in await cur.fetchall()]

    async def get_by_model(self, model_name: str) -> FinOpsModelPolicy | None:
        async with connect() as db:
            cur = await db.execute(
                f"{self._SELECT} WHERE model_name = ?", (model_name,)
            )
            row = await cur.fetchone()
            return _row_to_policy(row) if row else None

    async def save(self, policy: FinOpsModelPolicy) -> FinOpsModelPolicy:
        async with connect() as db:
            await db.execute(
                "INSERT INTO finops_model_policies (id, model_name, risk_tier, "
                "value_tier, max_cost_per_call, max_tokens_per_call, allowed_features, "
                "rationale, enabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(model_name) DO UPDATE SET "
                "  risk_tier = excluded.risk_tier, "
                "  value_tier = excluded.value_tier, "
                "  max_cost_per_call = excluded.max_cost_per_call, "
                "  max_tokens_per_call = excluded.max_tokens_per_call, "
                "  allowed_features = excluded.allowed_features, "
                "  rationale = excluded.rationale, "
                "  enabled = excluded.enabled, "
                "  updated_at = CURRENT_TIMESTAMP",
                (
                    str(policy.id),
                    policy.model_name,
                    policy.risk_tier.value,
                    policy.value_tier.value,
                    policy.max_cost_per_call,
                    policy.max_tokens_per_call,
                    json.dumps(policy.allowed_features) if policy.allowed_features is not None else None,
                    policy.rationale,
                    1 if policy.enabled else 0,
                ),
            )
            await db.commit()
            return policy

    async def delete(self, policy_id: UUID) -> bool:
        async with connect() as db:
            cur = await db.execute(
                "DELETE FROM finops_model_policies WHERE id = ?", (str(policy_id),)
            )
            await db.commit()
            return cur.rowcount > 0


# Helper público para criar UUID nas chamadas que não importam new_uuid.
__all__ = [
    "SqliteFinOpsRepository",
    "SqliteFinOpsBudgetRepository",
    "SqliteFinOpsModelPolicyRepository",
    "new_uuid",
]
