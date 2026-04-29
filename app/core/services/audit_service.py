"""Use case: Rastreabilidade (audit trail).

Registra TODAS as ações executadas na plataforma: chamadas HTTP, execuções
de módulo, mudanças de configuração, uploads, login, etc. Provê busca
paginada com filtros para a tela /audit.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.adapters.db.sqlite import connect


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


# Whitelist de janelas temporais aceitas em `since`. A string é interpolada
# direto em SQL (DATETIME('now', '-1 hours')) — fora desta whitelist seria
# vetor de injeção, então valores desconhecidos retornam None.
_SINCE_MAP = {
    "1h":  "-1 hours",
    "6h":  "-6 hours",
    "24h": "-1 days",
    "7d":  "-7 days",
    "30d": "-30 days",
}


def _since_to_sqlite(since: str) -> str | None:
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
    payload = None
    if row[10]:
        try:
            payload = json.loads(row[10])
        except (json.JSONDecodeError, TypeError):
            payload = {"raw": str(row[10])[:1000]}
    ts = row[1]
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            ts = datetime.utcnow()
    return AuditEvent(
        id=row[0], ts=ts, user_id=row[2], username=row[3],
        category=row[4], action=row[5], target=row[6], status_code=row[7],
        duration_ms=row[8], feature=row[9], payload=payload,
        error=row[11], ip=row[12], user_agent=row[13],
    )


_SELECT = (
    "SELECT id, ts, user_id, username, category, action, target, "
    "status_code, duration_ms, feature, payload, error, ip, user_agent "
    "FROM audit_events"
)


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
        clean_payload = None
        if payload is not None:
            try:
                clean_payload = json.dumps(_redact(payload), ensure_ascii=False, default=str)
                if len(clean_payload) > 100_000:
                    clean_payload = clean_payload[:100_000] + '"...TRUNCATED"}'
            except (TypeError, ValueError):
                clean_payload = json.dumps({"_serialize_error": str(payload)[:200]})

        async with connect() as db:
            await db.execute(
                "INSERT INTO audit_events (id, user_id, username, category, action, "
                "target, status_code, duration_ms, feature, payload, error, ip, user_agent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id, user_id, username, category, action, target,
                    status_code, duration_ms, feature, clean_payload, error,
                    ip, (user_agent or "")[:300],
                ),
            )
            await db.commit()
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
        where = []
        params: list = []
        if category:
            where.append("category = ?"); params.append(category)
        if feature:
            where.append("feature = ?"); params.append(feature)
        if username:
            where.append("username LIKE ?"); params.append(f"%{username}%")
        if status_min:
            where.append("status_code >= ?"); params.append(status_min)
        if q:
            where.append("(target LIKE ? OR action LIKE ? OR error LIKE ?)")
            like = f"%{q}%"; params.extend([like, like, like])
        if since:
            sql_since = _since_to_sqlite(since)
            if sql_since:
                where.append(f"ts >= DATETIME('now', '{sql_since}')")
        clause = (" WHERE " + " AND ".join(where)) if where else ""

        async with connect() as db:
            cur = await db.execute(f"SELECT COUNT(*) FROM audit_events{clause}", params)
            total = int((await cur.fetchone())[0])

            sql = f"{_SELECT}{clause} ORDER BY ts DESC"
            if per_page > 0:
                offset = max(0, (page - 1) * per_page)
                sql += " LIMIT ? OFFSET ?"
                params2 = params + [per_page, offset]
            else:
                # "todos" — cap defensivo em 5000 para não derrubar o navegador
                sql += " LIMIT 5000"
                params2 = params

            cur = await db.execute(sql, params2)
            events = [_row_to_event(r) for r in await cur.fetchall()]

        return {
            "events": events,
            "total": total,
            "page": page,
            "per_page": per_page,
        }

    async def get_event(self, event_id: str) -> AuditEvent | None:
        async with connect() as db:
            cur = await db.execute(f"{_SELECT} WHERE id = ?", (event_id,))
            row = await cur.fetchone()
            return _row_to_event(row) if row else None

    async def stats(self) -> dict:
        """Stats agregados para dashboard topo da tela."""
        async with connect() as db:
            cur = await db.execute(
                "SELECT COUNT(*), SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) "
                "FROM audit_events"
            )
            total, errors = await cur.fetchone()
            cur = await db.execute(
                "SELECT category, COUNT(*) FROM audit_events GROUP BY category ORDER BY 2 DESC"
            )
            by_category = [{"category": r[0], "count": int(r[1])} for r in await cur.fetchall()]
            cur = await db.execute(
                "SELECT feature, COUNT(*) FROM audit_events "
                "WHERE feature IS NOT NULL GROUP BY feature ORDER BY 2 DESC LIMIT 10"
            )
            by_feature = [{"feature": r[0], "count": int(r[1])} for r in await cur.fetchall()]
            cur = await db.execute(
                "SELECT username, COUNT(*) FROM audit_events "
                "WHERE username IS NOT NULL GROUP BY username ORDER BY 2 DESC LIMIT 10"
            )
            by_user = [{"username": r[0], "count": int(r[1])} for r in await cur.fetchall()]
            cur = await db.execute(
                "SELECT COUNT(*) FROM audit_events WHERE ts >= DATETIME('now', '-1 hour')"
            )
            last_hour = int((await cur.fetchone())[0])
            return {
                "total": int(total or 0),
                "errors": int(errors or 0),
                "last_hour": last_hour,
                "by_category": by_category,
                "by_feature": by_feature,
                "by_user": by_user,
            }


# singleton lazy
_global = AuditService()


def get_audit_service() -> AuditService:
    return _global
