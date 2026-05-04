"""Inicializa o banco SQLite (cria schema, seed e admin user)."""

import asyncio
import sys
from pathlib import Path

# Garante que o root do projeto esteja no sys.path quando o script for
# chamado de qualquer diretório.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.db.sqlite import init_db  # noqa: E402


def main():
    asyncio.run(init_db())
    print("Vértice — banco inicializado com sucesso.")


if __name__ == "__main__":
    main()
