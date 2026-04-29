"""Testes do ChurnService — taxonomia hierárquica e classificação em modo mock."""

import pytest

from app.adapters.db.repositories.churn_repo import SqliteChurnRepository
from app.adapters.db.repositories.finops_repo import SqliteFinOpsRepository
from app.adapters.db.sqlite import init_db
from app.adapters.guardrails.input_sanitizer import DefaultInputGuardrail
from app.adapters.guardrails.output_validator import DefaultOutputGuardrail
from app.adapters.llm.factory import build_clients
from app.core.services.churn_service import ChurnService
from app.core.services.model_router import ModelRouter


def _make_service() -> ChurnService:
    return ChurnService(
        churn=SqliteChurnRepository(),
        finops=SqliteFinOpsRepository(),
        router=ModelRouter(build_clients()),
        input_guard=DefaultInputGuardrail(),
        output_guard=DefaultOutputGuardrail(),
    )


@pytest.mark.asyncio
async def test_taxonomy_seeded():
    await init_db()
    svc = _make_service()
    roots = await svc.get_taxonomy()
    assert len(roots) > 0
    labels = [r.label for r in roots]
    assert "Preço" in labels


@pytest.mark.asyncio
async def test_add_and_remove_node():
    await init_db()
    svc = _make_service()
    roots_before = await svc.get_taxonomy()
    n = await svc.add_node(label="Teste-Node-Z")
    roots_after = await svc.get_taxonomy()
    assert any(r.label == "Teste-Node-Z" for r in roots_after)
    assert len(roots_after) == len(roots_before) + 1

    await svc.delete_node(n.id)
    roots_final = await svc.get_taxonomy()
    assert all(r.label != "Teste-Node-Z" for r in roots_final)


@pytest.mark.asyncio
async def test_classify_returns_path():
    await init_db()
    svc = _make_service()
    classification = await svc.classify(
        contract_number="X-CLASSIFY-1",
        transcript="Cliente diz que outro provedor é mais barato e quer cancelar.",
    )
    assert classification.path
    assert isinstance(classification.confidence, float)
