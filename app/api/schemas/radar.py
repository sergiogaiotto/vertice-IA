"""Schemas Pydantic do Radar Voz do Cliente."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ContractOut(BaseModel):
    contract_number: str
    call_id: str
    contact_id: str
    operator: str
    contact_at: datetime
    segment: str
    transcript_preview: str = ""


class CreateCardRequest(BaseModel):
    contract_number: str
    name: str
    prompt_text: str = Field(..., description="Prompt do usuário para a análise.")
    output_type: str = Field(..., description="SUMARIO|RESUMO|INTENCAO|UMA_PALAVRA|SCORE|TERMOS")
    expected_size: str = ""


class CardOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: str
    contract_number: str
    name: str
    output_type: str
    result: str
    model_used: str = ""
    confidence: float | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_estimated: float = 0.0
    created_at: datetime
