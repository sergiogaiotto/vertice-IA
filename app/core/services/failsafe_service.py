"""Use case: Failsafe / Human-in-the-loop."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.domain.entities import (
    FailsafeAction,
    FailsafeStatus,
    new_uuid,
)
from app.core.ports.repositories import FailsafeRepository


# valores aceitos para validar entrada de status nos filtros e create manual
_VALID_STATUSES = {s.value for s in FailsafeStatus}


class FailsafeService:
    """Gerencia ações que requerem aprovação humana antes de executar."""

    def __init__(self, repo: FailsafeRepository):
        self.repo = repo

    async def request(
        self,
        module_name: str,
        description: str,
        payload: dict[str, Any],
        confidence: float,
        requested_by: UUID | None = None,
    ) -> FailsafeAction:
        if not module_name or not module_name.strip():
            raise ValueError("module_name é obrigatório")
        if not description or not description.strip():
            raise ValueError("description é obrigatória")
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError("confidence deve estar entre 0.0 e 1.0")
        action = FailsafeAction(
            id=new_uuid(),
            module_name=module_name.strip(),
            description=description.strip(),
            payload=payload or {},
            confidence=confidence,
            status=FailsafeStatus.pending,
            requested_by=requested_by,
        )
        return await self.repo.save(action)

    async def list_pending(self) -> list[FailsafeAction]:
        return await self.repo.list_pending()

    async def list(
        self,
        status: str | None = None,
        module_name: str | None = None,
        q: str | None = None,
        page: int = 1,
        per_page: int = 30,
    ) -> dict[str, Any]:
        if status and status not in _VALID_STATUSES:
            raise ValueError(
                f"status inválido: {status}. Use {sorted(_VALID_STATUSES)}"
            )
        if per_page not in (10, 30, 100):
            raise ValueError("per_page deve ser 10, 30 ou 100")
        page = max(1, page)
        offset = (page - 1) * per_page
        items = await self.repo.list_filtered(
            status=status, module_name=module_name, q=q,
            limit=per_page, offset=offset,
        )
        total = await self.repo.count_filtered(
            status=status, module_name=module_name, q=q,
        )
        return {"items": items, "total": total, "page": page, "per_page": per_page}

    async def get(self, action_id: UUID) -> FailsafeAction:
        action = await self.repo.get(action_id)
        if not action:
            raise ValueError("ação não encontrada")
        return action

    async def update(
        self,
        action_id: UUID,
        description: str | None = None,
        payload: dict[str, Any] | None = None,
        confidence: float | None = None,
    ) -> FailsafeAction:
        """Edita campos da ação. Só permitido enquanto pendente: depois de
        decidida, a ação faz parte da trilha de auditoria e é imutável."""
        action = await self.repo.get(action_id)
        if not action:
            raise ValueError("ação não encontrada")
        if action.status != FailsafeStatus.pending:
            raise ValueError(
                f"ação já foi {action.status.value}; não pode mais ser editada"
            )
        if description is not None:
            if not description.strip():
                raise ValueError("description não pode ser vazia")
            action.description = description.strip()
        if payload is not None:
            action.payload = payload
        if confidence is not None:
            if confidence < 0.0 or confidence > 1.0:
                raise ValueError("confidence deve estar entre 0.0 e 1.0")
            action.confidence = confidence
        return await self.repo.save(action)

    async def decide(self, action_id: UUID, approve: bool, decided_by: UUID | None) -> FailsafeAction:
        action = await self.repo.get(action_id)
        if not action:
            raise ValueError("ação não encontrada")
        if action.status != FailsafeStatus.pending:
            raise ValueError(
                f"ação já foi {action.status.value}; decisão é definitiva"
            )
        action.status = FailsafeStatus.approved if approve else FailsafeStatus.rejected
        action.decided_by = decided_by
        return await self.repo.save(action)

    async def delete(self, action_id: UUID) -> None:
        """Remove ação do banco. Só permitido enquanto pendente, para
        preservar a trilha de auditoria de decisões já tomadas."""
        action = await self.repo.get(action_id)
        if not action:
            raise ValueError("ação não encontrada")
        if action.status != FailsafeStatus.pending:
            raise ValueError(
                f"ação já foi {action.status.value}; não pode ser apagada "
                "(faz parte da trilha de auditoria)"
            )
        if not await self.repo.delete(action_id):
            raise ValueError("ação não encontrada")

    async def stats(self) -> dict[str, Any]:
        counts = await self.repo.count_by_status()
        # garante chaves para todos os status conhecidos (mesmo se zero)
        return {
            "by_status": {s: counts.get(s, 0) for s in _VALID_STATUSES},
            "total": sum(counts.values()),
        }
