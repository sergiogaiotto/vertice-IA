"""Entidades do domínio."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


# ===== Auth & RBAC =====


@dataclass
class User:
    id: UUID
    username: str
    hashed_password: str
    salt: str
    full_name: str = ""
    email: str = ""
    phone: str = ""
    department: str = ""
    title: str = ""
    is_active: bool = True
    roles: list[str] = field(default_factory=list)


@dataclass
class Role:
    id: int
    name: str
    permissions: list[str] = field(default_factory=list)


# ===== Module Registry =====


class ModuleStatus(str, Enum):
    active = "active"
    paused = "paused"
    deprecated = "deprecated"


@dataclass
class Module:
    id: UUID
    name: str
    endpoint_url: str
    status: ModuleStatus = ModuleStatus.active
    config_params: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    skill_path: str | None = None
    response_type: str = "text"      # 'text' | 'api' | 'table'
    response_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApiEndpoint:
    """Endpoint HTTP externo configurado para uso por módulos response_type='api'."""
    id: UUID
    name: str
    url: str
    method: str = "POST"
    description: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 30
    is_active: bool = True
    created_by_user: str | None = None


# ===== Prompts (guardrail → system → guardrail) =====


@dataclass
class PromptBundle:
    """Tripla guardrail-entrada / system / guardrail-saída versionada.

    Um prompt pode atender a vários módulos — relação N:N via `module_names`.
    A property `module_name` (singular) retorna o primeiro item da lista,
    preservando compatibilidade com código legado que assume 1:1.
    """

    id: UUID
    name: str
    version: int
    input_guardrail: str
    system_prompt: str
    output_guardrail: str
    module_names: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    is_active: bool = True

    @property
    def module_name(self) -> str:
        return self.module_names[0] if self.module_names else ""


# ===== Radar Voz do Cliente =====


class OutputType(str, Enum):
    summary = "SUMARIO"
    resume = "RESUMO"
    intent = "INTENCAO"
    one_word = "UMA_PALAVRA"
    score = "SCORE"
    terms = "TERMOS"


class CustomerSegment(str, Enum):
    residential = "RESIDENCIAL"
    mobile = "MOVEL"
    partner = "PARCEIRO"
    high_value = "ALTO_VALOR"


@dataclass
class Contract:
    contract_number: str
    call_id: str
    contact_id: str
    operator: str
    contact_at: datetime
    segment: CustomerSegment = CustomerSegment.residential
    transcript: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalysisCard:
    """Card de análise produzido por um prompt sobre uma transcrição."""

    id: UUID
    contract_number: str
    name: str
    output_type: OutputType
    prompt_text: str
    expected_size: str = ""  # ex: "<= 50 palavras"
    model_used: str = ""
    result: str = ""
    confidence: float | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_estimated: float = 0.0
    created_at: datetime = field(default_factory=datetime.utcnow)


# ===== Churn (taxonomia hierárquica) =====


@dataclass
class ChurnNode:
    """Nó da taxonomia de churn — pode ter profundidade arbitrária."""

    id: UUID
    label: str
    parent_id: UUID | None = None
    depth: int = 0
    examples: list[str] = field(default_factory=list)
    occurrences: int = 0
    children: list["ChurnNode"] = field(default_factory=list)


@dataclass
class ChurnClassification:
    contract_number: str
    path: list[str]  # ex: ["preço", "plano caro", "competidor mais barato"]
    confidence: float
    rationale: str
    classified_at: datetime = field(default_factory=datetime.utcnow)


# ===== FinOps =====


@dataclass
class FinOpsEntry:
    id: int | None
    user_id: UUID | None
    module_id: UUID | None
    model_name: str
    tokens_input: int
    tokens_output: int
    cost_estimated: float
    context_tag: str = ""
    # Dimensões modernas — todas opcionais, callers antigos continuam válidos.
    domain: str | None = None          # ex.: 'voz_cliente', 'churn', 'finops'
    product: str | None = None         # ex.: 'radar', 'churn-classifier'
    agent: str | None = None           # ex.: 'sql_deep_agent', 'radar_intent'
    flow: str | None = None            # ex.: 'turn-3', 'classify->summarize'
    prompt_id: str | None = None
    integration: str | None = None     # ex.: 'verint-export', 'gmail-api'
    environment: str = "production"    # 'production'|'staging'|'dev'
    latency_ms: float | None = None
    storage_bytes: int | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)


class BudgetScopeType(str, Enum):
    global_ = "global"
    module = "module"
    model = "model"
    user = "user"
    domain = "domain"
    environment = "environment"
    agent = "agent"


class BudgetPeriod(str, Enum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"


class RiskTier(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ValueTier(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


@dataclass
class FinOpsBudget:
    id: UUID
    name: str
    scope_type: BudgetScopeType
    scope_value: str | None  # NULL para scope='global'
    period: BudgetPeriod
    limit_brl: float
    warning_threshold: float = 0.8   # fração 0..1 que dispara alerta amarelo
    hard_stop: bool = False          # se True, bloqueia chamadas após estouro
    notes: str | None = None
    created_by: UUID | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class FinOpsModelPolicy:
    id: UUID
    model_name: str
    risk_tier: RiskTier = RiskTier.medium
    value_tier: ValueTier = ValueTier.medium
    max_cost_per_call: float | None = None    # BRL — None = sem cap
    max_tokens_per_call: int | None = None
    allowed_features: list[str] | None = None  # None = todas; lista = whitelist
    rationale: str | None = None
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class FinOpsAlert:
    id: UUID
    budget_id: UUID
    severity: str                # 'warning'|'critical'
    cost_observed: float
    limit_reference: float
    period_start: datetime | None = None
    period_end: datetime | None = None
    triggered_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None


# ===== Failsafe =====


class FailsafeStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


@dataclass
class FailsafeAction:
    id: UUID
    module_name: str
    description: str
    payload: dict[str, Any]
    confidence: float
    status: FailsafeStatus = FailsafeStatus.pending
    requested_by: UUID | None = None
    decided_by: UUID | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)


# ===== BKO Inteligente =====


@dataclass
class BkoCase:
    """Caso do BKO carregado de planilha XLSX."""
    case_number: str
    created_by: str = ""
    owner: str = ""
    phone: str = ""
    opened_at: datetime | None = None
    contract_msisdn: str = ""


@dataclass
class TranscriptRecord:
    """Transcrição de chamada carregada de arquivo JSON do Verint/WhisperX."""
    transaction_id: str
    verint_nr_contrato: str = ""
    transcription_text: str = ""
    started_at: datetime | None = None
    duration_s: float = 0.0
    segment: str = ""
    msisdn: str = ""
    ani: str = ""
    cpf: str = ""
    employee: str = ""
    raw_json: str = ""


def new_uuid() -> UUID:
    return uuid4()
