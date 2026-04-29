"""Ports de persistência (repositórios)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.core.domain.entities import (
    AnalysisCard,
    ChurnClassification,
    ChurnNode,
    Contract,
    FailsafeAction,
    FinOpsAlert,
    FinOpsBudget,
    FinOpsEntry,
    FinOpsModelPolicy,
    Module,
    PromptBundle,
    User,
)


class UserRepository(ABC):
    @abstractmethod
    async def get_by_username(self, username: str) -> User | None: ...

    @abstractmethod
    async def get_by_id(self, user_id: UUID) -> User | None: ...

    @abstractmethod
    async def create(self, user: User) -> User: ...

    @abstractmethod
    async def list_all(self) -> list[User]: ...

    @abstractmethod
    async def set_active(self, user_id: UUID, active: bool) -> None: ...

    @abstractmethod
    async def set_roles(self, user_id: UUID, roles: list[str]) -> None: ...

    @abstractmethod
    async def set_password(self, user_id: UUID, hashed: str, salt: str) -> None: ...

    @abstractmethod
    async def delete(self, user_id: UUID) -> None: ...


class ModuleRepository(ABC):
    @abstractmethod
    async def list_active(self) -> list[Module]: ...

    @abstractmethod
    async def list_all(self) -> list[Module]: ...

    @abstractmethod
    async def get_by_name(self, name: str) -> Module | None: ...

    @abstractmethod
    async def get(self, module_id: UUID) -> Module | None: ...

    @abstractmethod
    async def upsert(self, module: Module) -> Module: ...

    @abstractmethod
    async def delete(self, module_id: UUID) -> None: ...


class PromptRepository(ABC):
    @abstractmethod
    async def list_for_module(self, module_name: str) -> list[PromptBundle]: ...

    @abstractmethod
    async def get_active(self, module_name: str, name: str) -> PromptBundle | None: ...

    @abstractmethod
    async def save(self, bundle: PromptBundle) -> PromptBundle: ...

    @abstractmethod
    async def get(self, prompt_id: UUID) -> PromptBundle | None: ...

    @abstractmethod
    async def list_all(self) -> list[PromptBundle]: ...

    @abstractmethod
    async def promote(self, prompt_id: UUID) -> None: ...

    @abstractmethod
    async def delete(self, prompt_id: UUID) -> None: ...

    @abstractmethod
    async def set_modules(self, prompt_id: UUID, module_names: list[str]) -> None: ...


class ContractRepository(ABC):
    @abstractmethod
    async def list_recent(self, limit: int = 200) -> list[Contract]: ...

    @abstractmethod
    async def get(self, contract_number: str) -> Contract | None: ...

    @abstractmethod
    async def bulk_upsert(self, contracts: list[Contract]) -> int: ...


class AnalysisRepository(ABC):
    @abstractmethod
    async def list_for_contract(self, contract_number: str) -> list[AnalysisCard]: ...

    @abstractmethod
    async def save(self, card: AnalysisCard) -> AnalysisCard: ...

    @abstractmethod
    async def delete(self, card_id: UUID) -> None: ...


class ChurnRepository(ABC):
    @abstractmethod
    async def get_taxonomy(self) -> list[ChurnNode]: ...

    @abstractmethod
    async def upsert_node(self, node: ChurnNode) -> ChurnNode: ...

    @abstractmethod
    async def delete_node(self, node_id: UUID) -> None: ...

    @abstractmethod
    async def save_classification(self, classification: ChurnClassification) -> None: ...

    @abstractmethod
    async def list_classifications(self, limit: int = 100) -> list[ChurnClassification]: ...


class FinOpsRepository(ABC):
    @abstractmethod
    async def append(self, entry: FinOpsEntry) -> FinOpsEntry: ...

    @abstractmethod
    async def aggregate_by_module(self) -> list[dict]: ...

    @abstractmethod
    async def aggregate_by_model(self) -> list[dict]: ...

    @abstractmethod
    async def session_total(self, session_id: str) -> float: ...

    @abstractmethod
    async def aggregate_by_day(self, days: int = 7) -> list[dict]: ...

    @abstractmethod
    async def totals(self) -> dict: ...

    @abstractmethod
    async def aggregate_by_dimension(self, dimension: str) -> list[dict]:
        """Agrega custo/tokens/calls por uma das dimensões finops:
        domain, product, agent, flow, prompt_id, integration, environment.
        """

    @abstractmethod
    async def current_spend(
        self,
        scope_type: str,
        scope_value: str | None,
        period: str,
    ) -> float:
        """Soma de custo no período atual para um determinado escopo. Usado
        pela engine de orçamentos para comparar com `limit_brl`."""


class FinOpsBudgetRepository(ABC):
    @abstractmethod
    async def list(self) -> list[FinOpsBudget]: ...

    @abstractmethod
    async def get(self, budget_id: UUID) -> FinOpsBudget | None: ...

    @abstractmethod
    async def save(self, budget: FinOpsBudget) -> FinOpsBudget: ...

    @abstractmethod
    async def delete(self, budget_id: UUID) -> bool: ...

    @abstractmethod
    async def append_alert(self, alert: FinOpsAlert) -> FinOpsAlert: ...

    @abstractmethod
    async def list_recent_alerts(self, limit: int = 20) -> list[FinOpsAlert]: ...


class FinOpsModelPolicyRepository(ABC):
    @abstractmethod
    async def list(self) -> list[FinOpsModelPolicy]: ...

    @abstractmethod
    async def get_by_model(self, model_name: str) -> FinOpsModelPolicy | None: ...

    @abstractmethod
    async def save(self, policy: FinOpsModelPolicy) -> FinOpsModelPolicy: ...

    @abstractmethod
    async def delete(self, policy_id: UUID) -> bool: ...


class FailsafeRepository(ABC):
    @abstractmethod
    async def list_pending(self) -> list[FailsafeAction]: ...

    @abstractmethod
    async def list_filtered(
        self,
        status: str | None = None,
        module_name: str | None = None,
        q: str | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> list[FailsafeAction]: ...

    @abstractmethod
    async def count_filtered(
        self,
        status: str | None = None,
        module_name: str | None = None,
        q: str | None = None,
    ) -> int: ...

    @abstractmethod
    async def count_by_status(self) -> dict[str, int]: ...

    @abstractmethod
    async def get(self, action_id: UUID) -> FailsafeAction | None: ...

    @abstractmethod
    async def save(self, action: FailsafeAction) -> FailsafeAction: ...

    @abstractmethod
    async def delete(self, action_id: UUID) -> bool: ...
