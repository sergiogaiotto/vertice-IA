"""Adaptador OPA (Open Policy Agent).

Se OPA_URL não estiver configurada, usa políticas locais simples.
"""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.core.ports.policy import PolicyEngine

settings = get_settings()


class OpaPolicyEngine(PolicyEngine):

    def __init__(self, opa_url: str | None = None):
        self.opa_url = (opa_url or settings.opa_url).rstrip("/")

    async def authorize(self, subject: dict, action: str, resource: dict) -> bool:
        # fallback local: admin sempre, demais segue ação
        if not self.opa_url:
            roles = subject.get("roles", [])
            if "admin" in roles:
                return True
            permission_map = {
                "execute:agent_analysis": {"analista_n3", "supervisor"},
                "manage:prompts": {"supervisor"},
                "manage:modules": {"admin"},
                "approve:failsafe": {"supervisor"},
                "view:finops": {"finops", "supervisor"},
            }
            allowed = permission_map.get(action, set())
            return any(r in allowed for r in roles)

        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{self.opa_url}/v1/data/vertice/authz/allow",
                json={"input": {"subject": subject, "action": action, "resource": resource}},
            )
            resp.raise_for_status()
            return bool(resp.json().get("result", False))

    async def route_model(self, intent: dict) -> str:
        if not self.opa_url:
            ot = (intent.get("output_type") or "").upper()
            if ot in {"UMA_PALAVRA", "SCORE", "TERMOS"}:
                return settings.router_cheap_model
            if ot in {"INTENCAO"}:
                return settings.router_fallback_model
            return settings.router_default_model

        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{self.opa_url}/v1/data/vertice/router/model",
                json={"input": intent},
            )
            resp.raise_for_status()
            return str(resp.json().get("result") or settings.router_default_model)
