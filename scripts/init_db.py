"""Inicializa o banco PostgreSQL (cria schema, seed, módulos default e
bootstrap da taxonomia churn).

Idempotente: pode ser executado tantas vezes quanto necessário."""

import asyncio
import sys
from pathlib import Path

# Garante que o root do projeto esteja no sys.path quando o script for
# chamado de qualquer diretório.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.db.postgres import close_pool, init_db  # noqa: E402


async def _run() -> None:
    try:
        await init_db()
        print("Vértice — banco PostgreSQL inicializado com sucesso.")
    finally:
        await close_pool()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
