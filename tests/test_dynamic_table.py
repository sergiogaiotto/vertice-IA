"""Tests do DynamicTableService — persistência de JSON de LLM em tabela dinâmica.

Foco: robustez contra *type-drift* do LLM. A mesma chave JSON chega com tipos
Python diferentes entre execuções; a versão antiga congelava o tipo da coluna no
primeiro valor e depois quebrava com:

    DataError: invalid input for query argument $N: 34.9 (expected str, got float)

A correção grava todas as colunas achatadas como TEXT (serialização estável),
guarda o payload original em `_raw` (JSONB) e auto-cura tabelas legadas tipadas.

Estes testes rodam sob o schema isolado do conftest (search_path != public), o
que também exercita a introspecção via `current_schema()`.
"""

from __future__ import annotations

import pytest

from app.adapters.db.postgres import connect, quote_ident
from app.core.services.dynamic_table_service import (
    _AUDIT_COLS,
    _RAW_COL,
    _flatten,
    _to_text,
    get_dynamic_table_service,
)

# ---------------------------------------------------------------------------
# Helpers puros (sem I/O)
# ---------------------------------------------------------------------------


def test_to_text_stable_serialization():
    assert _to_text(None) is None
    assert _to_text("já é string") == "já é string"
    assert _to_text(34.9) == "34.9"
    assert _to_text(10) == "10"
    assert _to_text(True) == "true"
    assert _to_text(False) == "false"


def test_flatten_dot_notation_and_lists():
    flat = _flatten({
        "cliente": {"nome": "X", "doc": "267.610.028-93"},
        "itens": ["a", "b"],
        "score": 34.9,
    })
    assert flat["cliente_nome"] == "X"
    assert flat["cliente_doc"] == "267.610.028-93"
    assert flat["itens"] == '["a", "b"]'
    assert flat["score"] == 34.9


def test_audit_cols_contract_unchanged_for_xlsx():
    """xlsx_import_service monta uma lista posicional de 7 valores de auditoria.
    `_raw` NÃO pode entrar em `_AUDIT_COLS` senão aquele INSERT quebra."""
    assert list(_AUDIT_COLS.keys()) == [
        "_id", "_ts", "_user_id", "_username",
        "_case_number", "_transaction_id", "_feature",
    ]
    assert _RAW_COL not in _AUDIT_COLS


# ---------------------------------------------------------------------------
# Integração com Postgres real (schema isolado do conftest)
# ---------------------------------------------------------------------------


async def _drop(table: str) -> None:
    async with connect() as db:
        await db.execute(f"DROP TABLE IF EXISTS {quote_ident(table)}")


@pytest.mark.asyncio
async def test_type_drift_text_then_float_is_the_reported_bug():
    """Repro exata do erro reportado: coluna nasce TEXT, depois chega float."""
    svc = get_dynamic_table_service()
    table = svc.table_name("estruturar_dados", "radar")
    await _drop(table)
    try:
        await svc.insert(table, {"valor_conta": "abc"}, "u1", "alice", "c1", "t1", "radar")
        # antes da correção: DataError (expected str, got float)
        rid = await svc.insert(table, {"valor_conta": 34.9}, "u1", "alice", "c2", "t2", "radar")
        assert rid
        rows = await svc.list_rows(table)
        vals = sorted(r["valor_conta"] for r in rows)
        assert vals == ["34.9", "abc"]  # ambos como TEXT
    finally:
        await _drop(table)


@pytest.mark.asyncio
async def test_type_drift_both_directions():
    """int→string e bool→string também não podem quebrar."""
    svc = get_dynamic_table_service()
    table = svc.table_name("estruturar_dados", "radar")
    await _drop(table)
    try:
        await svc.insert(table, {"score": 10, "ok": True}, "u", "a", "c1", "t1", "radar")
        await svc.insert(table, {"score": "N/A", "ok": "sim"}, "u", "a", "c2", "t2", "radar")
        await svc.insert(table, {"score": 3.14, "ok": False}, "u", "a", "c3", "t3", "radar")
        info = await svc.table_info(table)
        types = {c["name"]: c["type"] for c in info["columns"]}
        assert types["score"] == "TEXT"
        assert types["ok"] == "TEXT"
        assert info["row_count"] == 3
    finally:
        await _drop(table)


@pytest.mark.asyncio
async def test_legacy_typed_table_is_auto_healed():
    """Tabela criada antes da correção (coluna BIGINT tipada, sem `_raw`) deve
    se auto-curar: coluna alargada para TEXT e `_raw` adicionada."""
    svc = get_dynamic_table_service()
    table = svc.table_name("estruturar_dados", "radar")
    await _drop(table)
    try:
        async with connect() as db:
            audit_ddl = ", ".join(f"{quote_ident(c)} {t}" for c, t in _AUDIT_COLS.items())
            await db.execute(
                f"CREATE TABLE {quote_ident(table)} ({audit_ddl}, {quote_ident('score')} BIGINT)"
            )
            await db.execute(
                f"INSERT INTO {quote_ident(table)} (\"_id\", \"score\") VALUES ('legacy1', 7)"
            )
        # string num campo antes BIGINT — auto-widen precisa acontecer
        await svc.insert(table, {"score": "indefinido"}, "u", "a", "c", "t", "radar")
        info = await svc.table_info(table)
        types = {c["name"]: c["type"] for c in info["columns"]}
        assert types["score"] == "TEXT"
        assert _RAW_COL in types  # backfill
        rows = await svc.list_rows(table)
        assert sorted(r["score"] for r in rows) == ["7", "indefinido"]  # legado coerido p/ texto
    finally:
        await _drop(table)


@pytest.mark.asyncio
async def test_raw_jsonb_preserves_full_fidelity():
    """`_raw` guarda o payload estruturado original (aninhado, arrays, tipos)."""
    svc = get_dynamic_table_service()
    table = svc.table_name("estruturar_dados", "radar")
    await _drop(table)
    try:
        payload = {
            "atendimento_metadados": {"atendente_nome": "Fernanda", "protocolo": "2026-270-571-963"},
            "cliente": {"email": None, "doc": "267.610.028-93"},
            "score": 34.9,
            "resolvido": True,
            "itens": ["a", "b", 3],
        }
        rid = await svc.insert(table, payload, "u", "a", "c", "t", "radar")
        async with connect() as db:
            row = dict(await db.fetchrow(
                f"SELECT * FROM {quote_ident(table)} WHERE _id = $1", rid
            ))
        # fidelidade total no _raw
        assert row[_RAW_COL] == payload
        # colunas achatadas em TEXT
        assert row["atendimento_metadados_atendente_nome"] == "Fernanda"
        assert row["score"] == "34.9"
        assert row["resolvido"] == "true"
        assert row["cliente_email"] is None
        assert row["itens"] == '["a", "b", 3]'
    finally:
        await _drop(table)


@pytest.mark.asyncio
async def test_new_keys_add_columns_idempotently():
    """Chaves novas em execuções seguintes viram colunas TEXT sem quebrar."""
    svc = get_dynamic_table_service()
    table = svc.table_name("estruturar_dados", "radar")
    await _drop(table)
    try:
        await svc.insert(table, {"a": "1"}, "u", "x", "c1", "t1", "radar")
        await svc.insert(table, {"a": "2", "b": "novo"}, "u", "x", "c2", "t2", "radar")
        info = await svc.table_info(table)
        names = {c["name"] for c in info["columns"]}
        assert {"a", "b"}.issubset(names)
        assert info["row_count"] == 2
    finally:
        await _drop(table)


@pytest.mark.asyncio
async def test_non_dict_payload_does_not_crash():
    """Payload que não é dict (lista/topo) só grava auditoria + _raw, sem erro."""
    svc = get_dynamic_table_service()
    table = svc.table_name("estruturar_dados", "radar")
    await _drop(table)
    try:
        rid = await svc.insert(table, ["x", "y"], "u", "x", "c", "t", "radar")
        async with connect() as db:
            row = dict(await db.fetchrow(
                f"SELECT * FROM {quote_ident(table)} WHERE _id = $1", rid
            ))
        assert row[_RAW_COL] == ["x", "y"]
    finally:
        await _drop(table)
