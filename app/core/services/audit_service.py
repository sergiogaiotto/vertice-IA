"""Use case: Rastreabilidade (audit trail).

Registra TODAS as ações executadas na plataforma: chamadas HTTP, execuções
de módulo, mudanças de configuração, uploads, login, etc. Provê busca
paginada com filtros para a tela /audit.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.adapters.db.postgres import connect


# Path → feature (heurística para tagging automático de eventos HTTP)
_FEATURE_PATTERNS = [
    (re.compile(r"^/api/radar/"), "radar"),
    (re.compile(r"^/api/churn/"), "churn"),
    (re.compile(r"^/api/prompts"), "prompts"),
    (re.compile(r"^/api/skills"), "skills"),
    (re.compile(r"^/api/modules"), "modules"),
    (re.compile(r"^/api/blocks"), "modules"),
    (re.compile(r"^/api/finops"), "finops"),
    (re.compile(r"^/api/failsafe"), "failsafe"),
    (re.compile(r"^/api/users"), "users"),
    (re.compile(r"^/api/auth"), "auth"),
    (re.compile(r"^/api/presentations"), "presentations"),
    (re.compile(r"^/api/api-endpoints"), "apis"),
    (re.compile(r"^/radar"), "radar"),
    (re.compile(r"^/churn"), "churn"),
    (re.compile(r"^/prompts"), "prompts"),
    (re.compile(r"^/skills"), "skills"),
    (re.compile(r"^/modules"), "modules"),
    (re.compile(r"^/blocks"), "modules"),
    (re.compile(r"^/finops"), "finops"),
    (re.compile(r"^/failsafe"), "failsafe"),
    (re.compile(r"^/users"), "users"),
    (re.compile(r"^/audit"), "audit"),
    (re.compile(r"^/gallery"), "presentations"),
    (re.compile(r"^/apis"), "apis"),
]

# campos sensíveis que NUNCA vão para o payload do audit
_REDACT_FIELDS = {"password", "new_password", "old_password", "token", "secret", "api_key"}


def detect_feature(path: str) -> str | None:
    for pattern, feature in _FEATURE_PATTERNS:
        if pattern.match(path or ""):
            return feature
    return None


# Whitelist de janelas temporais aceitas em `since`. O valor (intervalo PG)
# é interpolado direto via cast `::interval` parametrizado — fora desta
# whitelist seria vetor de injeção, então valores desconhecidos retornam None.
_SINCE_MAP = {
    "1h":  "1 hour",
    "6h":  "6 hours",
    "24h": "1 day",
    "7d":  "7 days",
    "30d": "30 days",
}


def _since_to_interval(since: str) -> str | None:
    return _SINCE_MAP.get((since or "").strip().lower())


def _redact(obj: Any, depth: int = 0) -> Any:
    """Recursivamente substitui valores de campos sensíveis por '***REDACTED***'."""
    if depth > 8:
        return "<...>"
    if isinstance(obj, dict):
        return {
            k: ("***REDACTED***" if k.lower() in _REDACT_FIELDS else _redact(v, depth + 1))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(x, depth + 1) for x in obj[:50]]  # cap em 50 itens
    if isinstance(obj, str) and len(obj) > 8000:
        return obj[:8000] + f"…<truncated {len(obj) - 8000} chars>"
    return obj


@dataclass
class AuditEvent:
    id: str
    ts: datetime
    user_id: str | None
    username: str | None
    category: str
    action: str
    target: str | None
    status_code: int | None
    duration_ms: float | None
    feature: str | None
    payload: dict | None
    error: str | None
    ip: str | None
    user_agent: str | None


def _row_to_event(row) -> AuditEvent:
    payload = row["payload"]
    if payload is not None and not isinstance(payload, (dict, list)):
        payload = {"raw": str(payload)[:1000]}
    ts = row["ts"] if isinstance(row["ts"], datetime) else datetime.utcnow()
    return AuditEvent(
        id=row["id"],
        ts=ts,
        user_id=row["user_id"],
        username=row["username"],
        category=row["category"],
        action=row["action"],
        target=row["target"],
        status_code=row["status_code"],
        duration_ms=row["duration_ms"],
        feature=row["feature"],
        payload=payload,
        error=row["error"],
        ip=row["ip"],
        user_agent=row["user_agent"],
    )


_SELECT = (
    "SELECT id::text AS id, ts, user_id::text AS user_id, username, category, "
    "action, target, status_code, duration_ms, feature, payload, error, ip, "
    "user_agent FROM audit_events"
)


# Cap de tamanho do payload — JSONB no Postgres aguenta vários MBs, mas
# linhas gigantes prejudicam vacuum/index e ficam difíceis de auditar.
_PAYLOAD_MAX_BYTES = 100_000


class AuditService:

    async def record(
        self,
        category: str,
        action: str,
        target: str | None = None,
        user_id: str | None = None,
        username: str | None = None,
        status_code: int | None = None,
        duration_ms: float | None = None,
        feature: str | None = None,
        payload: dict | None = None,
        error: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> str:
        """Persiste um evento. Retorna o id."""
        event_id = uuid.uuid4().hex
        clean_payload: Any = None
        if payload is not None:
            try:
                clean_payload = _redact(payload)
                # estima tamanho via repr; trunca payload em campo único se
                # ficar gigante.
                approx = len(repr(clean_payload))
                if approx > _PAYLOAD_MAX_BYTES:
                    clean_payload = {"_truncated": True, "_size": approx}
            except (TypeError, ValueError):
                clean_payload = {"_serialize_error": str(payload)[:200]}

        async with connect() as db:
            await db.execute(
                "INSERT INTO audit_events (id, user_id, username, category, "
                "action, target, status_code, duration_ms, feature, payload, "
                "error, ip, user_agent) "
                "VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, "
                "        $10::jsonb, $11, $12, $13)",
                event_id, user_id, username, category, action, target,
                status_code, duration_ms, feature, clean_payload, error,
                ip, (user_agent or "")[:300],
            )
        return event_id

    async def list_events(
        self,
        page: int = 1,
        per_page: int = 30,
        category: str | None = None,
        feature: str | None = None,
        username: str | None = None,
        status_min: int | None = None,
        q: str | None = None,
        since: str | None = None,
    ) -> dict:
        """Lista paginada com filtros. Use per_page=-1 para todos.

        ``since`` aceita janelas relativas: ``1h``, ``24h``, ``7d``, ``30d``.
        """
        where: list[str] = []
        params: list = []
        if category:
            params.append(category); where.append(f"category = ${len(params)}")
        if feature:
            params.append(feature); where.append(f"feature = ${len(params)}")
        if username:
            params.append(f"%{username}%")
            where.append(f"username ILIKE ${len(params)}")
        if status_min:
            params.append(status_min)
            where.append(f"status_code >= ${len(params)}")
        if q:
            params.append(f"%{q}%")
            where.append(
                f"(target ILIKE ${len(params)} "
                f" OR action ILIKE ${len(params)} "
                f" OR error ILIKE ${len(params)})"
            )
        if since:
            interval = _since_to_interval(since)
            if interval:
                params.append(interval)
                where.append(f"ts >= NOW() - ${len(params)}::interval")
        clause = (" WHERE " + " AND ".join(where)) if where else ""

        async with connect() as db:
            total = await db.fetchval(
                f"SELECT COUNT(*) FROM audit_events{clause}", *params
            )
            total = int(total or 0)

            sql = f"{_SELECT}{clause} ORDER BY ts DESC"
            if per_page > 0:
                offset = max(0, (page - 1) * per_page)
                params2 = params + [per_page, offset]
                sql += f" LIMIT ${len(params2) - 1} OFFSET ${len(params2)}"
            else:
                # "todos" — cap defensivo em 5000.
                sql += " LIMIT 5000"
                params2 = params

            rows = await db.fetch(sql, *params2)
            events = [_row_to_event(r) for r in rows]

        return {
            "events": events,
            "total": total,
            "page": page,
            "per_page": per_page,
        }

    async def get_event(self, event_id: str) -> AuditEvent | None:
        async with connect() as db:
            row = await db.fetchrow(f"{_SELECT} WHERE id = $1::uuid", event_id)
            return _row_to_event(row) if row else None

    async def stats(self) -> dict:
        """Stats agregados para dashboard topo da tela."""
        async with connect() as db:
            row = await db.fetchrow(
                "SELECT COUNT(*) AS total, "
                "       SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS errors "
                "FROM audit_events"
            )
            cat_rows = await db.fetch(
                "SELECT category, COUNT(*) AS n FROM audit_events "
                "GROUP BY category ORDER BY n DESC"
            )
            feat_rows = await db.fetch(
                "SELECT feature, COUNT(*) AS n FROM audit_events "
                "WHERE feature IS NOT NULL GROUP BY feature ORDER BY n DESC LIMIT 10"
            )
            user_rows = await db.fetch(
                "SELECT username, COUNT(*) AS n FROM audit_events "
                "WHERE username IS NOT NULL GROUP BY username "
                "ORDER BY n DESC LIMIT 10"
            )
            last_hour = await db.fetchval(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE ts >= NOW() - INTERVAL '1 hour'"
            )
            return {
                "total": int(row["total"] or 0),
                "errors": int(row["errors"] or 0),
                "last_hour": int(last_hour or 0),
                "by_category": [{"category": r["category"], "count": int(r["n"])} for r in cat_rows],
                "by_feature":  [{"feature":  r["feature"],  "count": int(r["n"])} for r in feat_rows],
                "by_user":     [{"username": r["username"], "count": int(r["n"])} for r in user_rows],
            }


# singleton lazy
_global = AuditService()


def get_audit_service() -> AuditService:
    return _global
