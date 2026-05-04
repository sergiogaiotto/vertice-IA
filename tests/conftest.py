"""Fixtures globais."""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

# Banco isolado por sessão de teste
@pytest.fixture(scope="session", autouse=True)
def _temp_db(monkeypatch_session):
    tmp = Path(tempfile.mkdtemp()) / "vertice_test.db"
    monkeypatch_session.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    yield


@pytest.fixture(scope="session")
def monkeypatch_session():
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
