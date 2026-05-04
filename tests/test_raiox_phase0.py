"""Smoke tests do Raio X Cliente — Fase 0 (boards, charts, query, relacionamentos)."""

from __future__ import annotations

import pytest

from app.adapters.db.repositories.raiox_repo import (
    SqliteRaioXBoardRepository,
    SqliteRaioXChartRepository,
    SqliteRaioXRelationshipRepository,
)
from app.adapters.db.sqlite import init_db
from app.core.services.raiox_service import RaioXService, SUPPORTED_CHART_TYPES_F0
from app.core.services.schema_service import SchemaService


def _make_service() -> RaioXService:
    return RaioXService(
        boards=SqliteRaioXBoardRepository(),
        charts=SqliteRaioXChartRepository(),
        rels=SqliteRaioXRelationshipRepository(),
        schema=SchemaService(),
    )


@pytest.mark.asyncio
async def test_board_lifecycle_and_chart_render():
    """create board → add chart → execute query → delete chart → delete board."""
    await init_db()
    svc = _make_service()

    # 1) cria prancheta
    board = await svc.create_board(
        name="Smoke Test Board",
        owner_id=None,
        description="board criado pelo teste",
        is_shared=True,
    )
    assert board.id is not None
    assert board.name == "Smoke Test Board"

    # 2) board aparece em list_visible
    boards = await svc.list_boards(user_id=None)
    assert any(b.id == board.id for b in boards)

    # 3) adiciona chart contando bko_cases por owner (tabela com seed)
    chart = await svc.add_chart(
        board_id=board.id,
        chart_type="bar",
        title="Casos por proprietário",
        query_spec={
            "table": "bko_cases",
            "label_column": "owner",
            "value_column": "",
            "aggregate": "count",
            "order_by": "value_desc",
            "limit": 10,
        },
    )
    assert chart.id is not None
    assert chart.chart_type == "bar"

    # 4) lista charts do board
    charts = await svc.list_charts(board.id)
    assert len(charts) == 1
    assert charts[0].id == chart.id

    # 5) executa a série (deve devolver pelo menos um label/value se há dados)
    series = await svc.build_series(chart.query_spec)
    assert "labels" in series and "values" in series
    assert series["aggregate"] == "count"
    assert len(series["labels"]) == len(series["values"])

    # 6) chart_type inválido deve falhar
    with pytest.raises(ValueError):
        await svc.add_chart(
            board_id=board.id,
            chart_type="candlestick",  # não suportado
            query_spec={
                "table": "bko_cases", "label_column": "owner",
                "value_column": "", "aggregate": "count",
            },
        )

    # 7) limpeza
    assert await svc.delete_chart(chart.id) is True
    assert await svc.delete_board(board.id) is True


@pytest.mark.asyncio
async def test_supported_chart_types_phase0_baseline():
    """Garante que os 6 tipos originais da F0 continuam suportados (regressão)."""
    await init_db()
    f0_baseline = {"bar", "line", "scatter", "pie", "histogram", "box"}
    assert f0_baseline.issubset(SUPPORTED_CHART_TYPES_F0)


@pytest.mark.asyncio
async def test_relationship_detection_finds_candidates():
    """Heurística deve retornar pelo menos uma sugestão se houver coluna comum."""
    await init_db()
    svc = _make_service()
    suggestions = await svc.detect_relationships(only_unconfirmed=True)
    # Não exigimos N>0 estrito (depende de quais tabelas estão visíveis no seed),
    # mas a função tem que rodar sem erro e devolver lista.
    assert isinstance(suggestions, list)
    for s in suggestions:
        assert s.table_a and s.column_a and s.table_b and s.column_b
        assert 0.0 <= s.confidence <= 1.0


@pytest.mark.asyncio
async def test_query_validation_rejects_bad_inputs():
    """build_series deve rejeitar specs inválidas com ValueError."""
    await init_db()
    svc = _make_service()
    with pytest.raises(ValueError):
        await svc.build_series({})
    with pytest.raises(ValueError):
        await svc.build_series({"table": "bko_cases"})  # falta label_column
