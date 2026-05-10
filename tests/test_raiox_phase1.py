"""Tests da Fase 1 do Raio X: catálogo expandido, filtros, joins, crossfilter shape."""

from __future__ import annotations

import pytest

from app.adapters.db.postgres import init_db
from app.adapters.db.repositories.raiox_repo import (
    PgRaioXBoardRepository,
    PgRaioXChartRepository,
    PgRaioXRelationshipRepository,
)
from app.core.domain.entities import RaioXRelationship, new_uuid
from app.core.services.raiox_service import RaioXService, SUPPORTED_CHART_TYPES
from app.core.services.schema_service import SchemaService


def _make_service() -> RaioXService:
    return RaioXService(
        boards=PgRaioXBoardRepository(),
        charts=PgRaioXChartRepository(),
        rels=PgRaioXRelationshipRepository(),
        schema=SchemaService(),
    )


@pytest.mark.asyncio
async def test_catalog_expanded_to_phase1():
    """Catálogo F1 cobre todos os tipos do dropdown."""
    expected = {
        "bar", "line", "scatter", "pie", "histogram", "box",
        "donut", "treemap", "sunburst", "funnel", "violin", "area",
        "heatmap", "waterfall", "indicator",
    }
    assert SUPPORTED_CHART_TYPES == expected


@pytest.mark.asyncio
async def test_chart_with_new_type_persists():
    """Tipos da F1 (sunburst, treemap, indicator) devem ser aceitos no add_chart."""
    await init_db()
    svc = _make_service()
    board = await svc.create_board("F1 Catalog Test", owner_id=None)
    for ct in ("sunburst", "treemap", "indicator", "donut", "violin"):
        chart = await svc.add_chart(
            board_id=board.id, chart_type=ct,
            query_spec={
                "table": "bko_cases", "label_column": "owner",
                "value_column": "", "aggregate": "count",
            },
        )
        assert chart.chart_type == ct
    await svc.delete_board(board.id)


@pytest.mark.asyncio
async def test_filter_predicate_narrows_series():
    """Filter '=' deve reduzir a série àquele label específico."""
    await init_db()
    svc = _make_service()
    # Sem filtro: pode ter vários labels
    full = await svc.build_series({
        "table": "bko_cases", "label_column": "owner",
        "aggregate": "count", "order_by": "value_desc", "limit": 10,
    })
    if not full["labels"]:
        pytest.skip("sem dados em bko_cases para filtrar")
    pinned = full["labels"][0]
    # Com filtro: só o owner pinado
    filtered = await svc.build_series({
        "table": "bko_cases", "label_column": "owner",
        "aggregate": "count", "order_by": "value_desc", "limit": 10,
        "filters": [{"column": "owner", "op": "=", "value": pinned}],
    })
    assert filtered["labels"] == [pinned]
    assert filtered["values"][0] > 0


@pytest.mark.asyncio
async def test_filter_invalid_column_rejected():
    """Coluna fora do schema deve ser rejeitada."""
    await init_db()
    svc = _make_service()
    with pytest.raises(ValueError):
        await svc.build_series({
            "table": "bko_cases", "label_column": "owner",
            "aggregate": "count",
            "filters": [{"column": "owner; DROP TABLE users--", "op": "=", "value": "x"}],
        })


@pytest.mark.asyncio
async def test_filter_unsupported_op_rejected():
    """Apenas op '=' nesta fase."""
    await init_db()
    svc = _make_service()
    with pytest.raises(ValueError):
        await svc.build_series({
            "table": "bko_cases", "label_column": "owner",
            "aggregate": "count",
            "filters": [{"column": "owner", "op": "LIKE", "value": "x%"}],
        })


@pytest.mark.asyncio
async def test_join_requires_whitelisted_relationship():
    """Sem relationship registrado, join é rejeitado."""
    await init_db()
    svc = _make_service()
    # Garante estado limpo (testes anteriores podem ter deixado rels no DB)
    for r in await svc.list_relationships():
        await svc.delete_relationship(r.id)
    with pytest.raises(ValueError, match="raiox_relationships"):
        await svc.build_series({
            "table": "bko_cases", "label_column": "owner",
            "aggregate": "count",
            "joins": [{
                "from_table": "bko_cases", "from_column": "contract_msisdn",
                "to_table": "transcripts", "to_column": "verint_nr_contrato",
            }],
        })


@pytest.mark.asyncio
async def test_join_works_after_relationship_registered():
    """Com relationship registrado, join produz série válida."""
    await init_db()
    svc = _make_service()
    rel = RaioXRelationship(
        id=new_uuid(),
        table_a="bko_cases", column_a="contract_msisdn",
        table_b="transcripts", column_b="verint_nr_contrato",
        kind="one_to_many", confidence=1.0,
    )
    await svc.save_relationship(rel)
    # Conta transcripts agrupadas por owner do BKO via JOIN
    result = await svc.build_series({
        "table": "bko_cases",
        "label_column": "bko_cases.owner",
        "value_column": "transcripts.transaction_id",
        "aggregate": "count",
        "order_by": "value_desc", "limit": 5,
        "joins": [{
            "from_table": "bko_cases", "from_column": "contract_msisdn",
            "to_table": "transcripts", "to_column": "verint_nr_contrato",
        }],
    })
    assert "labels" in result and "values" in result
    assert result["aggregate"] == "count"
    # remove a relação para não vazar entre testes
    await svc.delete_relationship(rel.id)


@pytest.mark.asyncio
async def test_join_rejects_invalid_identifier():
    """Identificador SQL inválido não passa pela whitelist."""
    await init_db()
    svc = _make_service()
    with pytest.raises(ValueError):
        await svc.build_series({
            "table": "bko_cases", "label_column": "owner",
            "aggregate": "count",
            "joins": [{
                "from_table": "bko_cases", "from_column": "contract_msisdn",
                "to_table": "transcripts; DROP TABLE users--",
                "to_column": "x",
            }],
        })
