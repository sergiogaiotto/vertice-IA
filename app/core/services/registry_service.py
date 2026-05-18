"""Use case: Module Registry."""

from __future__ import annotations

import time
from uuid import UUID

import httpx

from app.core.domain.entities import Module, ModuleStatus, new_uuid
from app.core.ports.repositories import ModuleRepository


class RegistryService:
    def __init__(self, modules: ModuleRepository):
        self.modules = modules

    async def list_active(self) -> list[Module]:
        return await self.modules.list_active()

    async def list_all(self) -> list[Module]:
        return await self.modules.list_all()

    async def get(self, module_id: UUID) -> Module | None:
        return await self.modules.get(module_id)

    async def get_by_name(self, name: str) -> Module | None:
        return await self.modules.get_by_name(name)

    @staticmethod
    def _compute_endpoint(name: str) -> str:
        """Endpoint sempre derivado do nome — Standard Module Contract.

        A plataforma é autoritativa sobre a URL: o cliente NÃO pode definir
        outra. Garante consistência (1 nome = 1 endpoint) e evita que o usuário
        crie módulos órfãos apontando para URLs inválidas.
        """
        import re
        slug = re.sub(r"[^a-z0-9_-]+", "_", (name or "").lower()).strip("_")
        return f"/api/{slug}/v1/process" if slug else ""

    async def register(
        self,
        name: str,
        endpoint_url: str = "",
        description: str = "",
        config_params: dict | None = None,
        skill_path: str | None = None,
        response_type: str = "text",
        response_config: dict | None = None,
        knowledge_base_id: UUID | None = None,
    ) -> Module:
        # endpoint_url do parâmetro é IGNORADO — sempre computa a partir do nome
        canonical_endpoint = self._compute_endpoint(name)
        existing = await self.modules.get_by_name(name)
        module = existing or Module(
            id=new_uuid(),
            name=name,
            endpoint_url=canonical_endpoint,
            status=ModuleStatus.active,
            config_params=config_params or {},
            description=description,
            skill_path=skill_path,
            response_type=response_type or "text",
            response_config=response_config or {},
            knowledge_base_id=knowledge_base_id,
        )
        if existing:
            module.endpoint_url = canonical_endpoint
            module.config_params = config_params or {}
            module.description = description
            module.skill_path = skill_path
            module.response_type = response_type or "text"
            module.response_config = response_config or {}
            module.knowledge_base_id = knowledge_base_id
        return await self.modules.upsert(module)

    # Sentinel para distinguir "campo ausente" de "set explícito como None".
    # update() recebe `knowledge_base_id=_UNSET` quando o caller não passou
    # o campo (não muda). Quando recebe None ou UUID, atualiza.
    _UNSET = object()

    async def update(
        self,
        module_id: UUID,
        endpoint_url: str | None = None,  # ignorado — endpoint é derivado do nome
        description: str | None = None,
        config_params: dict | None = None,
        skill_path: str | None = None,
        status: str | None = None,
        response_type: str | None = None,
        response_config: dict | None = None,
        knowledge_base_id=_UNSET,
    ) -> Module:
        m = await self.modules.get(module_id)
        if not m:
            raise ValueError("módulo não encontrado")
        m.endpoint_url = self._compute_endpoint(m.name)
        if description is not None:
            m.description = description
        if config_params is not None:
            m.config_params = config_params
        if skill_path is not None:
            m.skill_path = skill_path
        if status is not None:
            m.status = ModuleStatus(status)
        if response_type is not None:
            m.response_type = response_type
        if response_config is not None:
            m.response_config = response_config
        if knowledge_base_id is not self._UNSET:
            m.knowledge_base_id = knowledge_base_id
        return await self.modules.upsert(m)

    async def set_status(self, module_id: UUID, status: ModuleStatus) -> Module:
        m = await self.modules.get(module_id)
        if not m:
            raise ValueError("módulo não encontrado")
        m.status = status
        return await self.modules.upsert(m)

    async def delete(self, module_id: UUID) -> None:
        await self.modules.delete(module_id)

    async def health_check(self, module_id: UUID, base_url: str = "http://localhost:8000") -> dict:
        m = await self.modules.get(module_id)
        if not m:
            raise ValueError("módulo não encontrado")
        url = m.endpoint_url
        if url.startswith("/"):
            url = base_url.rstrip("/") + url
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                # OPTIONS é mais leve e não dispara o pipeline real
                resp = await client.options(url)
                latency_ms = round((time.perf_counter() - start) * 1000, 1)
                return {
                    "ok": resp.status_code < 500,
                    "status_code": resp.status_code,
                    "latency_ms": latency_ms,
                    "url": url,
                }
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "status_code": None,
                "latency_ms": round((time.perf_counter() - start) * 1000, 1),
                "url": url,
                "error": str(e)[:200],
            }
