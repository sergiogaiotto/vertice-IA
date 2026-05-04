"""Port para o motor de políticas (OPA)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class PolicyEngine(ABC):
    @abstractmethod
    async def authorize(self, subject: dict, action: str, resource: dict) -> bool: ...

    @abstractmethod
    async def route_model(self, intent: dict) -> str:
        """Devolve o nome do modelo a usar a partir de uma intenção."""
        ...
