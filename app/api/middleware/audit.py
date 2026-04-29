"""Middleware ASGI que registra toda chamada HTTP no audit trail.

Captura: method, path, status, duração, IP, user agent, e — para POST/PATCH/PUT
JSON — uma snapshot do body (com redact de campos sensíveis).
"""

from __future__ import annotations

import json
import time
from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.services.audit_service import detect_feature, get_audit_service


# paths que NÃO entram no audit (ruído puro)
_SKIP_PREFIXES = (
    "/static/",
    "/favicon",
    "/api/finops/summary",   # cost pulse bar bate de 30 em 30s
    "/api/audit",            # auto-rastreio do próprio audit polui o log
)

# paths/categorias para uploads (geram payload separado, não tenta ler o body)
_BINARY_HINT_PATHS = ("/upload", "/artifacts/")


def _categorize(method: str, path: str) -> str:
    if path.startswith("/api/auth"):
        return "auth"
    if "/run-module" in path or "/chat" in path:
        return "module_run"
    if any(h in path for h in _BINARY_HINT_PATHS):
        return "upload" if "upload" in path else "download"
    if path.startswith("/api/"):
        if method == "GET":
            return "http_read"
        return "http_write"
    return "page_view"


class AuditMiddleware(BaseHTTPMiddleware):

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)

        method = request.method
        start = time.perf_counter()

        # captura body para POST/PATCH/PUT (lê e re-injeta no scope)
        body_snapshot = None
        is_json = "application/json" in (request.headers.get("content-type") or "")
        is_binary = any(h in path for h in _BINARY_HINT_PATHS) or "multipart/form-data" in (request.headers.get("content-type") or "")
        if method in ("POST", "PATCH", "PUT") and is_json and not is_binary:
            try:
                raw = await request.body()
                if raw and len(raw) < 50_000:  # cap pra não estourar memória
                    body_snapshot = json.loads(raw.decode("utf-8"))

                # re-injeta o body no receive() para os handlers downstream lerem
                async def receive():
                    return {"type": "http.request", "body": raw, "more_body": False}
                request._receive = receive  # type: ignore[attr-defined]
            except Exception:
                body_snapshot = None

        # tentativa de extrair username do request.state (depende do auth middleware)
        # como o auth resolve via cookie no handler, tentamos extrair depois.
        error = None
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
            status_code = 500
            raise
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)

            # username + user_id — leitura best-effort do cookie de sessão
            username = None
            user_id = None
            try:
                user = getattr(request.state, "user", None)
                if user:
                    username = getattr(user, "username", None)
                    uid = getattr(user, "id", None)
                    user_id = str(uid) if uid else None
            except Exception:  # noqa: BLE001
                pass

            payload: dict = {"method": method, "path": path}
            if request.query_params:
                payload["query"] = dict(request.query_params)
            if body_snapshot is not None:
                payload["body"] = body_snapshot

            try:
                await get_audit_service().record(
                    category=_categorize(method, path),
                    action=method,
                    target=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    feature=detect_feature(path),
                    payload=payload,
                    error=error,
                    user_id=user_id,
                    username=username,
                    ip=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                )
            except Exception:  # noqa: BLE001
                # auditoria nunca pode quebrar a resposta — engole erros
                pass

        return response
