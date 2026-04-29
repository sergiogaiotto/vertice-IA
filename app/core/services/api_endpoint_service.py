"""Use case: API Endpoints externos.

CRUD de URLs HTTP que módulos response_type='api' chamam, e executor que
faz a chamada com timeout e auditoria automática em api_calls.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.adapters.db.sqlite import connect


@dataclass
class ApiEndpointDTO:
    id: str
    name: str
    description: str
    url: str
    method: str
    headers: dict
    timeout_seconds: int
    is_active: bool
    created_by_user: str
    created_at: datetime


def _row_to_dto(row) -> ApiEndpointDTO:
    return ApiEndpointDTO(
        id=row[0], name=row[1], description=row[2] or "",
        url=row[3], method=row[4] or "POST",
        headers=json.loads(row[5]) if row[5] else {},
        timeout_seconds=int(row[6] or 30),
        is_active=bool(row[7]),
        created_by_user=row[8] or "",
        created_at=datetime.fromisoformat(row[9]) if isinstance(row[9], str) else (row[9] or datetime.utcnow()),
    )


_SELECT = (
    "SELECT id, name, description, url, method, headers, timeout_seconds, "
    "is_active, created_by_user, created_at FROM api_endpoints"
)


class ApiEndpointService:

    async def list_all(self, only_active: bool = False) -> list[ApiEndpointDTO]:
        clause = " WHERE is_active = 1" if only_active else ""
        async with connect() as db:
            cur = await db.execute(f"{_SELECT}{clause} ORDER BY name")
            return [_row_to_dto(r) for r in await cur.fetchall()]

    async def get(self, endpoint_id: str) -> ApiEndpointDTO | None:
        async with connect() as db:
            cur = await db.execute(f"{_SELECT} WHERE id = ?", (endpoint_id,))
            row = await cur.fetchone()
            return _row_to_dto(row) if row else None

    async def create(
        self, name: str, url: str, method: str = "POST",
        description: str = "", headers: dict | None = None,
        timeout_seconds: int = 30, created_by_user: str = "",
    ) -> ApiEndpointDTO:
        eid = uuid.uuid4().hex
        async with connect() as db:
            await db.execute(
                "INSERT INTO api_endpoints (id, name, description, url, method, headers, "
                "timeout_seconds, created_by_user) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (eid, name, description, url, method.upper(),
                 json.dumps(headers or {}), timeout_seconds, created_by_user),
            )
            await db.commit()
        return await self.get(eid)

    async def update(
        self, endpoint_id: str, name: str, url: str, method: str,
        description: str, headers: dict, timeout_seconds: int, is_active: bool,
    ) -> ApiEndpointDTO | None:
        async with connect() as db:
            await db.execute(
                "UPDATE api_endpoints SET name=?, description=?, url=?, method=?, "
                "headers=?, timeout_seconds=?, is_active=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?",
                (name, description, url, method.upper(),
                 json.dumps(headers or {}), timeout_seconds, int(is_active), endpoint_id),
            )
            await db.commit()
        return await self.get(endpoint_id)

    async def delete(self, endpoint_id: str) -> None:
        async with connect() as db:
            await db.execute("DELETE FROM api_endpoints WHERE id = ?", (endpoint_id,))
            await db.commit()

    async def call(
        self,
        endpoint: ApiEndpointDTO,
        body: dict,
        module_id: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        """Executa chamada HTTP e devolve {ok, status, body, duration_ms, error}."""
        call_id = uuid.uuid4().hex
        start = time.perf_counter()
        result = {
            "ok": False, "status": None, "body": None,
            "duration_ms": 0.0, "error": None, "call_id": call_id,
        }
        try:
            async with httpx.AsyncClient(timeout=endpoint.timeout_seconds) as client:
                resp = await client.request(
                    endpoint.method, endpoint.url,
                    json=body if endpoint.method in ("POST", "PUT", "PATCH") else None,
                    params=body if endpoint.method == "GET" else None,
                    headers=endpoint.headers or {},
                )
            result["status"] = resp.status_code
            try:
                result["body"] = resp.json()
            except Exception:
                result["body"] = resp.text[:5000]
            result["ok"] = 200 <= resp.status_code < 300
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
        finally:
            result["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)

        # registra em api_calls
        try:
            body_str = json.dumps(body, ensure_ascii=False, default=str)[:50000]
            resp_str = json.dumps(result["body"], ensure_ascii=False, default=str)[:50000] if result["body"] else None
            async with connect() as db:
                await db.execute(
                    "INSERT INTO api_calls (id, api_endpoint_id, module_id, user_id, "
                    "request_body, response_status, response_body, duration_ms, error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (call_id, endpoint.id, module_id, user_id,
                     body_str, result["status"], resp_str,
                     result["duration_ms"], result["error"]),
                )
                await db.commit()
        except Exception:
            pass

        return result


_global = ApiEndpointService()


def get_api_endpoint_service() -> ApiEndpointService:
    return _global
