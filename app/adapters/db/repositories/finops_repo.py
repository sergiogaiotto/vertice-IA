"""Repositório PostgreSQL do FinOps Ledger + Orçamentos + Políticas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.adapters.db.postgres import connect
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


# Whitelist de dimensões expostas para chargeback/showback. Foi definida
# explicitamente para evitar SQL injection na composição dinâmica e para
# garantir que apenas colunas indexadas/seguras sejam agregadas.
#
# `module` deriva do prefixo do `context_tag` antes da '/' — usa SPLIT_PART
# do PostgreSQL (mais legível e mais rápido que SUBSTR(..., INSTR(...))).
_VALID_DIMENSIONS = {
    "module":      "NULLIF(SPLIT_PART(context_tag, '/', 1), '')",
    "model":       "model_name",
    "domain":      "domain",
    "product":     "product",
    "agent":       "agent",
    "flow":        "flow",
    "prompt_id":   "prompt_id",
    "integration": "integration",
    "environment": "environment",
}


# Início do período corrente em PostgreSQL — `date_trunc` é o canônico para
# "começo do dia/semana/mês". 'week' começa segunda-feira (ISO 8601).
_PERIOD_START_SQL = {
    "daily":   "date_trunc('day', NOW())",
    "weekly":  "date_trunc('week', NOW())",
    "monthly": "date_trunc('month', NOW())",
}


def _row_to_budget(row) -> FinOpsBudget:
    return FinOpsBudget(
        id=UUID(row["id"]),
        name=row["name"],
        scope_type=BudgetScopeType(row["scope_type"]),
        scope_value=row["scope_value"],
        period=BudgetPeriod(row["period"]),
        limit_brl=float(row["limit_brl"] or 0.0),
        warning_threshold=float(row["warning_threshold"] or 0.8),
        hard_stop=bool(row["hard_stop"]),
        notes=row["notes"],
        created_by=UUID(row["created_by"]) if row["created_by"] else None,
    )


def _row_to_policy(row) -> FinOpsModelPolicy:
    features = row["allowed_features"]
    if not isinstance(features, list):
        features = None
    return FinOpsModelPolicy(
        id=UUID(row["id"]),
        model_name=row["model_name"],
        risk_tier=RiskTier(row["risk_tier"]),
        value_tier=ValueTier(row["value_tier"]),
        max_cost_per_call=float(row["max_cost_per_call"]) if row["max_cost_per_call"] is not None else None,
        max_tokens_per_call=int(row["max_tokens_per_call"]) if row["max_tokens_per_call"] is not None else None,
        allowed_features=features,
        rationale=row["rationale"],
        enabled=bool(row["enabled"]),
    )


def _row_to_alert(row) -> FinOpsAlert:
    return FinOpsAlert(
        id=UUID(row["id"]),
        budget_id=UUID(row["budget_id"]),
        severity=row["severity"],
        cost_observed=float(row["cost_observed"] or 0.0),
        limit_reference=float(row["limit_reference"] or 0.0),
        period_start=row["period_start"],
        period_end=row["period_end"],
        triggered_at=row["triggered_at"] if isinstance(row["triggered_at"], datetime) else datetime.utcnow(),
        resolved_at=row["resolved_at"],
    )


class PgFinOpsRepository(FinOpsRepository):

    async def append(self, entry: FinOpsEntry) -> FinOpsEntry:
        async with connect() as db:
            new_id = await db.fetchval(
                "INSERT INTO finops_ledger (user_id, module_id, model_name, "
                "tokens_input, tokens_output, cost_estimated, context_tag, "
                "domain, product, agent, flow, prompt_id, integration, "
                "environment, latency_ms, storage_bytes) "
                "VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, "
                "$11, $12, $13, $14, $15, $16) "
                "RETURNING id",
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
            )
            entry.id = int(new_id)
            return entry

    async def aggregate_by_module(self) -> list[dict]:
        async with connect() as db:
            rows = await db.fetch(
                "SELECT COALESCE(NULLIF(SPLIT_PART(context_tag, '/', 1), ''), "
                "       context_tag) AS module_name, "
                "  SUM(tokens_input) AS tokens_in, "
                "  SUM(tokens_output) AS tokens_out, "
                "  SUM(cost_estimated) AS cost, "
                "  COUNT(*) AS calls "
                "FROM finops_ledger GROUP BY module_name "
                "ORDER BY cost DESC NULLS LAST"
            )
            return [
                {
                    "module": (r["module_name"] or "outros"),
                    "tokens_input": int(r["tokens_in"] or 0),
                    "tokens_output": int(r["tokens_out"] or 0),
                    "cost": float(r["cost"] or 0),
                    "calls": int(r["calls"] or 0),
                }
                for r in rows
            ]

    async def aggregate_by_model(self) -> list[dict]:
        async with connect() as db:
            rows = await db.fetch(
                "SELECT model_name, SUM(tokens_input) AS tin, "
                "  SUM(tokens_output) AS tout, SUM(cost_estimated) AS cost, "
                "  COUNT(*) AS calls "
                "FROM finops_ledger "
                "GROUP BY model_name "
                "ORDER BY SUM(cost_estimated) DESC NULLS LAST"
            )
            return [
                {
                    "model": r["model_name"] or "?",
                    "tokens_input": int(r["tin"] or 0),
                    "tokens_output": int(r["tout"] or 0),
                    "cost": float(r["cost"] or 0),
                    "calls": int(r["calls"] or 0),
                }
                for r in rows
            ]

    async def session_total(self, session_id: str) -> float:
        async with connect() as db:
            v = await db.fetchval(
                "SELECT SUM(cost_estimated) FROM finops_ledger "
                "WHERE context_tag LIKE $1",
                f"%{session_id}%",
            )
            return float(v or 0.0)

    async def aggregate_by_day(self, days: int = 7) -> list[dict]:
        """Agrega custo/calls por dia nos últimos N dias (mais recente primeiro)."""
        async with connect() as db:
            rows = await db.fetch(
                "SELECT to_char(created_at, 'YYYY-MM-DD') AS day, "
                "  SUM(cost_estimated) AS cost, "
                "  SUM(tokens_input + tokens_output) AS tokens, "
                "  COUNT(*) AS calls "
                "FROM finops_ledger "
                "WHERE created_at >= NOW() - ($1::int * INTERVAL '1 day') "
                "GROUP BY day "
                "ORDER BY day DESC",
                days,
            )
            return [
                {
                    "day": r["day"],
                    "cost": float(r["cost"] or 0),
                    "tokens": int(r["tokens"] or 0),
                    "calls": int(r["calls"] or 0),
                }
                for r in rows
            ]

    async def totals(self) -> dict:
        """Totais globais para os KPIs do Cockpit."""
        async with connect() as db:
            row = await db.fetchrow(
                "SELECT COUNT(*) AS calls, "
                "  SUM(cost_estimated) AS cost, "
                "  SUM(tokens_input + tokens_output) AS tokens "
                "FROM finops_ledger"
            )
            row24 = await db.fetchrow(
                "SELECT COUNT(*) AS calls, SUM(cost_estimated) AS cost "
                "FROM finops_ledger "
                "WHERE created_at >= NOW() - INTERVAL '1 day'"
            )
            return {
                "calls_total":  int(row["calls"] or 0),
                "cost_total":   float(row["cost"] or 0),
                "tokens_total": int(row["tokens"] or 0),
                "calls_24h":    int(row24["calls"] or 0),
                "cost_24h":     float(row24["cost"] or 0),
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
            rows = await db.fetch(
                f"SELECT {expr} AS bucket, "
                "  SUM(tokens_input) AS tin, SUM(tokens_output) AS tout, "
                "  SUM(cost_estimated) AS cost, COUNT(*) AS calls, "
                "  AVG(latency_ms) AS avg_latency "
                "FROM finops_ledger "
                f"GROUP BY {expr} "
                "ORDER BY cost DESC NULLS LAST"
            )
            return [
                {
                    "bucket": (r["bucket"] or "(sem rateio)"),
                    "tokens_input": int(r["tin"] or 0),
                    "tokens_output": int(r["tout"] or 0),
                    "cost": float(r["cost"] or 0.0),
                    "calls": int(r["calls"] or 0),
                    "avg_latency_ms": float(r["avg_latency"]) if r["avg_latency"] is not None else None,
                }
                for r in rows
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
        col_map = {
            "global":      None,
            "module":      "NULLIF(SPLIT_PART(context_tag, '/', 1), '')",
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
            # `user_id` é UUID — cast explícito para asyncpg aceitar string.
            cast = "::uuid" if col == "user_id" else ""
            clause += f" AND {col} = $1{cast}"
            params.append(scope_value)
        async with connect() as db:
            v = await db.fetchval(
                f"SELECT COALESCE(SUM(cost_estimated), 0) FROM finops_ledger "
                f"WHERE {clause}",
                *params,
            )
            return float(v or 0.0)


# ---------------------------------------------------------------------------
# Orçamentos
# ---------------------------------------------------------------------------


class PgFinOpsBudgetRepository(FinOpsBudgetRepository):

    _SELECT = (
        "SELECT id::text AS id, name, scope_type, scope_value, period, "
        "limit_brl, warning_threshold, hard_stop, notes, "
        "created_by::text AS created_by FROM finops_budgets"
    )

    async def list(self) -> list[FinOpsBudget]:
        async with connect() as db:
            rows = await db.fetch(f"{self._SELECT} ORDER BY created_at DESC")
            return [_row_to_budget(r) for r in rows]

    async def get(self, budget_id: UUID) -> FinOpsBudget | None:
        async with connect() as db:
            row = await db.fetchrow(
                f"{self._SELECT} WHERE id = $1::uuid", str(budget_id)
            )
            return _row_to_budget(row) if row else None

    async def save(self, budget: FinOpsBudget) -> FinOpsBudget:
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO finops_budgets (id, name, scope_type, scope_value,
                                            period, limit_brl, warning_threshold,
                                            hard_stop, notes, created_by)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10::uuid)
                ON CONFLICT (id) DO UPDATE SET
                    name              = EXCLUDED.name,
                    scope_type        = EXCLUDED.scope_type,
                    scope_value       = EXCLUDED.scope_value,
                    period            = EXCLUDED.period,
                    limit_brl         = EXCLUDED.limit_brl,
                    warning_threshold = EXCLUDED.warning_threshold,
                    hard_stop         = EXCLUDED.hard_stop,
                    notes             = EXCLUDED.notes,
                    updated_at        = NOW()
                """,
                str(budget.id), budget.name, budget.scope_type.value,
                budget.scope_value, budget.period.value, budget.limit_brl,
                budget.warning_threshold, budget.hard_stop, budget.notes,
                str(budget.created_by) if budget.created_by else None,
            )
            return budget

    async def delete(self, budget_id: UUID) -> bool:
        async with connect() as db:
            result = await db.execute(
                "DELETE FROM finops_budgets WHERE id = $1::uuid", str(budget_id)
            )
            return result.endswith(" 1")  # asyncpg retorna 'DELETE N'

    async def append_alert(self, alert: FinOpsAlert) -> FinOpsAlert:
        async with connect() as db:
            await db.execute(
                "INSERT INTO finops_alerts (id, budget_id, severity, "
                "cost_observed, limit_reference, period_start, period_end) "
                "VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7)",
                str(alert.id), str(alert.budget_id), alert.severity,
                alert.cost_observed, alert.limit_reference,
                alert.period_start, alert.period_end,
            )
            return alert

    async def list_recent_alerts(self, limit: int = 20) -> list[FinOpsAlert]:
        async with connect() as db:
            rows = await db.fetch(
                "SELECT id::text AS id, budget_id::text AS budget_id, severity, "
                "  cost_observed, limit_reference, period_start, period_end, "
                "  triggered_at, resolved_at "
                "FROM finops_alerts ORDER BY triggered_at DESC LIMIT $1",
                limit,
            )
            return [_row_to_alert(r) for r in rows]


# ---------------------------------------------------------------------------
# Políticas de modelo
# ---------------------------------------------------------------------------


class PgFinOpsModelPolicyRepository(FinOpsModelPolicyRepository):

    _SELECT = (
        "SELECT id::text AS id, model_name, risk_tier, value_tier, "
        "max_cost_per_call, max_tokens_per_call, allowed_features, "
        "rationale, enabled FROM finops_model_policies"
    )

    async def list(self) -> list[FinOpsModelPolicy]:
        async with connect() as db:
            rows = await db.fetch(f"{self._SELECT} ORDER BY model_name ASC")
            return [_row_to_policy(r) for r in rows]

    async def get_by_model(self, model_name: str) -> FinOpsModelPolicy | None:
        async with connect() as db:
            row = await db.fetchrow(
                f"{self._SELECT} WHERE model_name = $1", model_name
            )
            return _row_to_policy(row) if row else None

    async def save(self, policy: FinOpsModelPolicy) -> FinOpsModelPolicy:
        async with connect() as db:
            await db.execute(
                """
                INSERT INTO finops_model_policies (id, model_name, risk_tier,
                                                   value_tier, max_cost_per_call,
                                                   max_tokens_per_call,
                                                   allowed_features, rationale,
                                                   enabled)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)
                ON CONFLICT (model_name) DO UPDATE SET
                    risk_tier            = EXCLUDED.risk_tier,
                    value_tier           = EXCLUDED.value_tier,
                    max_cost_per_call    = EXCLUDED.max_cost_per_call,
                    max_tokens_per_call  = EXCLUDED.max_tokens_per_call,
                    allowed_features     = EXCLUDED.allowed_features,
                    rationale            = EXCLUDED.rationale,
                    enabled              = EXCLUDED.enabled,
                    updated_at           = NOW()
                """,
                str(policy.id), policy.model_name, policy.risk_tier.value,
                policy.value_tier.value, policy.max_cost_per_call,
                policy.max_tokens_per_call, policy.allowed_features,
                policy.rationale, policy.enabled,
            )
            return policy

    async def delete(self, policy_id: UUID) -> bool:
        async with connect() as db:
            result = await db.execute(
                "DELETE FROM finops_model_policies WHERE id = $1::uuid",
                str(policy_id),
            )
            return result.endswith(" 1")


# Helper público para criar UUID nas chamadas que não importam new_uuid.
__all__ = [
    "PgFinOpsRepository",
    "PgFinOpsBudgetRepository",
    "PgFinOpsModelPolicyRepository",
    "new_uuid",
]
