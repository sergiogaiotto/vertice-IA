"""Dependency Injection — fábricas centralizadas para os routers."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from app.adapters.db.repositories.analysis_repo import SqliteAnalysisRepository
from app.adapters.db.repositories.churn_repo import SqliteChurnRepository
from app.adapters.db.repositories.contract_repo import SqliteContractRepository
from app.adapters.db.repositories.failsafe_repo import SqliteFailsafeRepository
from app.adapters.db.repositories.finops_repo import (
    SqliteFinOpsBudgetRepository,
    SqliteFinOpsModelPolicyRepository,
    SqliteFinOpsRepository,
)
from app.adapters.db.repositories.module_repo import SqliteModuleRepository
from app.adapters.db.repositories.prompt_repo import SqlitePromptRepository
from app.adapters.db.repositories.user_repo import SqliteUserRepository
from app.adapters.guardrails.input_sanitizer import DefaultInputGuardrail
from app.adapters.guardrails.output_validator import DefaultOutputGuardrail
from app.adapters.llm.factory import build_clients
from app.adapters.observability.composite_tracer import CompositeTracer
from app.adapters.policy.opa_adapter import OpaPolicyEngine
from app.core.domain.entities import User
from app.core.services.auth_service import AuthService
from app.core.services.churn_service import ChurnService
from app.core.services.failsafe_service import FailsafeService
from app.core.services.finops_service import (
    CostAwareRouter,
    FinOpsBudgetService,
    FinOpsPolicyService,
    FinOpsService,
)
from app.core.services.model_router import ModelRouter
from app.core.services.module_wizard_service import ModuleWizardService
from app.core.services.skill_wizard_service import SkillWizardService
from app.core.services.prompt_service import PromptService
from app.core.services.radar_service import RadarService
from app.core.services.registry_service import RegistryService
from app.core.services.skill_service import SkillService
from app.core.services.user_admin_service import UserAdminService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


# ---------- singletons leves ----------

@lru_cache
def get_input_guardrail():
    return DefaultInputGuardrail()


@lru_cache
def get_output_guardrail():
    return DefaultOutputGuardrail()


@lru_cache
def get_tracer():
    return CompositeTracer()


@lru_cache
def get_policy():
    return OpaPolicyEngine()


@lru_cache
def get_router_clients():
    return ModelRouter(build_clients())


# ---------- repositories ----------

def get_user_repo():
    return SqliteUserRepository()


def get_module_repo():
    return SqliteModuleRepository()


def get_prompt_repo():
    return SqlitePromptRepository()


def get_contract_repo():
    return SqliteContractRepository()


def get_analysis_repo():
    return SqliteAnalysisRepository()


def get_churn_repo():
    return SqliteChurnRepository()


def get_finops_repo():
    return SqliteFinOpsRepository()


def get_failsafe_repo():
    return SqliteFailsafeRepository()


# ---------- services ----------

def get_auth_service(users=Depends(get_user_repo)) -> AuthService:
    return AuthService(users)


def get_radar_service(
    contracts=Depends(get_contract_repo),
    analyses=Depends(get_analysis_repo),
    finops=Depends(get_finops_repo),
) -> RadarService:
    return RadarService(
        contracts=contracts,
        analyses=analyses,
        finops=finops,
        router=get_router_clients(),
        input_guard=get_input_guardrail(),
        output_guard=get_output_guardrail(),
    )


def get_churn_service(
    churn=Depends(get_churn_repo),
    finops=Depends(get_finops_repo),
) -> ChurnService:
    return ChurnService(
        churn=churn,
        finops=finops,
        router=get_router_clients(),
        input_guard=get_input_guardrail(),
        output_guard=get_output_guardrail(),
    )


def get_prompt_service(prompts=Depends(get_prompt_repo)) -> PromptService:
    return PromptService(prompts)


def get_finops_service(finops=Depends(get_finops_repo)) -> FinOpsService:
    return FinOpsService(finops)


def get_finops_budget_repo():
    return SqliteFinOpsBudgetRepository()


def get_finops_policy_repo():
    return SqliteFinOpsModelPolicyRepository()


def get_finops_budget_service(
    budget_repo=Depends(get_finops_budget_repo),
    finops_repo=Depends(get_finops_repo),
) -> FinOpsBudgetService:
    return FinOpsBudgetService(budget_repo, finops_repo)


def get_finops_policy_service(
    repo=Depends(get_finops_policy_repo),
) -> FinOpsPolicyService:
    return FinOpsPolicyService(repo)


def get_cost_aware_router(
    policy_svc=Depends(get_finops_policy_service),
    budget_svc=Depends(get_finops_budget_service),
) -> CostAwareRouter:
    return CostAwareRouter(policy_svc, budget_svc)


def get_failsafe_service(repo=Depends(get_failsafe_repo)) -> FailsafeService:
    return FailsafeService(repo)


def get_registry_service(modules=Depends(get_module_repo)) -> RegistryService:
    return RegistryService(modules)


def get_user_admin_service(users=Depends(get_user_repo)) -> UserAdminService:
    return UserAdminService(users)


@lru_cache
def get_skill_service() -> SkillService:
    return SkillService()


@lru_cache
def get_module_wizard_service() -> ModuleWizardService:
    return ModuleWizardService(llms=build_clients(), skills=SkillService())


@lru_cache
def get_skill_wizard_service() -> SkillWizardService:
    return SkillWizardService(llms=build_clients())


def get_bko_service():
    from app.core.services.bko_service import BkoService
    return BkoService()


def get_schema_service():
    from app.core.services.schema_service import SchemaService
    return SchemaService()


# ---------- auth helpers ----------

async def current_user_optional(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    auth: AuthService = Depends(get_auth_service),
) -> Optional[User]:
    # tenta token Bearer; se ausente, tenta cookie de sessão
    actual = token or request.session.get("token")
    if not actual:
        return None
    user = await auth.current_user(actual)
    # publica em request.state para o AuditMiddleware capturar
    if user:
        request.state.user = user
    return user


async def require_user(user: Optional[User] = Depends(current_user_optional)) -> User:
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="autenticação requerida")
    return user
