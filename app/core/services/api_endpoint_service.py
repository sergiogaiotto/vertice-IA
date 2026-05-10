"""Use case: API Endpoints externos.

CRUD de URLs HTTP que módulos response_type='api' chamam, e executor que
faz a chamada com timeout e auditoria automática em api_calls.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.adapters.db.postgres import connect


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
    headers = row["headers"] if isinstance(row["headers"], dict) else {}
    created = row["created_at"] if isinstance(row["created_at"], datetime) else datetime.utcnow()
    return ApiEndpointDTO(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        url=row["url"],
        method=row["method"] or "POST",
        headers=headers,
        timeout_seconds=int(row["timeout_seconds"] or 30),
        is_active=bool(row["is_active"]),
        created_by_user=row["created_by_user"] or "",
        created_at=created,
    )


_SELECT = (
    "SELECT id::text AS id, name, description, url, method, headers, "
    "timeout_seconds, is_active, created_by_user, created_at "
    "FROM api_endpoints"
)


class ApiEndpointService:

    async def list_all(self, only_active: bool = False) -> list[ApiEndpointDTO]:
        clause = " WHERE is_active = TRUE" if only_active else ""
        async with connect() as db:
            rows = await db.fetch(f"{_SELECT}{clause} ORDER BY name")
            return [_row_to_dto(r) for r in rows]

    async def get(self, endpoint_id: str) -> ApiEndpointDTO | None:
        async with connect() as db:
            row = await db.fetchrow(
                f"{_SELECT} WHERE id = $1::uuid", endpoint_id
            )
            return _row_to_dto(row) if row else None

    async def create(
        self, name: str, url: str, method: str = "POST",
        description: str = "", headers: dict | None = None,
        timeout_seconds: int = 30, created_by_user: str = "",
    ) -> ApiEndpointDTO:
        eid = uuid.uuid4().hex
        async with connect() as db:
            await db.execute(
                "INSERT INTO api_endpoints (id, name, description, url, method, "
                "headers, timeout_seconds, created_by_user) "
                "VALUES ($1::uuid, $2, $3, $4, $5, $6::jsonb, $7, $8)",
                eid, name, description, url, method.upper(),
                headers or {}, timeout_seconds, created_by_user,
            )
        return await self.get(eid)

    async def update(
        self, endpoint_id: str, name: str, url: str, method: str,
        description: str, headers: dict, timeout_seconds: int, is_active: bool,
    ) -> ApiEndpointDTO | None:
        async with connect() as db:
            await db.execute(
                "UPDATE api_endpoints SET name=$1, description=$2, url=$3, "
                "method=$4, headers=$5::jsonb, timeout_seconds=$6, is_active=$7, "
                "updated_at=NOW() WHERE id=$8::uuid",
                name, description, url, method.upper(),
                headers or {}, timeout_seconds, is_active, endpoint_id,
            )
        return await self.get(endpoint_id)

    async def delete(self, endpoint_id: str) -> None:
        async with connect() as db:
            await db.execute(
                "DELETE FROM api_endpoints WHERE id = $1::uuid", endpoint_id
            )

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

        # registra em api_calls (asyncpg encoda dict→jsonb diretamente)
        try:
            response_payload = result["body"]
            # Garante que o body fique como dict/list em JSONB; fallback para
            # string em campo artificial se for texto cru.
            if response_payload is not None and not isinstance(response_payload, (dict, list)):
                response_payload = {"_text": str(response_payload)[:50000]}
            async with connect() as db:
                await db.execute(
                    "INSERT INTO api_calls (id, api_endpoint_id, module_id, "
                    "user_id, request_body, response_status, response_body, "
                    "duration_ms, error) "
                    "VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::jsonb, "
                    "        $6, $7::jsonb, $8, $9)",
                    call_id, endpoint.id, module_id, user_id,
                    body or {}, result["status"], response_payload,
                    result["duration_ms"], result["error"],
                )
        except Exception:
            pass

        return result


_global = ApiEndpointService()


def get_api_endpoint_service() -> ApiEndpointService:
    return _global
