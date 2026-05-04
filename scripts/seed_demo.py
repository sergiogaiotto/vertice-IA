"""Seed de dados demo — contratos sintéticos para o Radar.

Útil para clicar pela plataforma sem depender de upload Excel.
"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Garante que o root do projeto esteja no sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.db.repositories.contract_repo import SqliteContractRepository  # noqa: E402
from app.adapters.db.sqlite import init_db  # noqa: E402
from app.core.domain.entities import Contract, CustomerSegment  # noqa: E402

DEMO = [
    {
        "contract_number": "4471-882",
        "call_id": "8841",
        "contact_id": "C-99812",
        "operator": "Renata M.",
        "segment": CustomerSegment.mobile,
        "transcript": (
            "OPERADOR: Olá, em que posso ajudar?\n"
            "CLIENTE: Boa tarde. Vocês me cobraram duas vezes pelo roaming da viagem que fiz para o Chile.\n"
            "OPERADOR: Vou verificar... De fato consta cobrança duplicada de R$ 184,90.\n"
            "CLIENTE: Eu quero que devolvam imediatamente, não em duas faturas.\n"
            "OPERADOR: Posso oferecer estorno em dois ciclos. É a política atual.\n"
            "CLIENTE: Não aceito. Se for assim, vou portar meu número para a concorrência.\n"
            "OPERADOR: Vou escalar para retenção, um momento.\n"
        ),
    },
    {
        "contract_number": "3320-117",
        "call_id": "9012",
        "contact_id": "C-44120",
        "operator": "Caio S.",
        "segment": CustomerSegment.residential,
        "transcript": (
            "CLIENTE: Minha internet residencial está caindo toda noite às 21h.\n"
            "OPERADOR: Sinto muito. Já houve algum técnico no local?\n"
            "CLIENTE: Sim, três vezes este mês. Sem solução.\n"
            "OPERADOR: Posso abrir um chamado prioritário e oferecer 30% de desconto na próxima fatura.\n"
            "CLIENTE: O desconto não me interessa. Quero que resolvam ou eu cancelo.\n"
        ),
    },
    {
        "contract_number": "9988-005",
        "call_id": "7766",
        "contact_id": "C-77123",
        "operator": "Fernanda L.",
        "segment": CustomerSegment.high_value,
        "transcript": (
            "CLIENTE: Recebi uma proposta de outra operadora com plano 30% mais barato.\n"
            "OPERADOR: Entendo. Posso oferecer um match na sua próxima renovação com bônus de dados.\n"
            "CLIENTE: Vou pensar e retorno.\n"
        ),
    },
]


async def seed():
    await init_db()
    repo = SqliteContractRepository()
    contracts = []
    base = datetime.now() - timedelta(hours=1)
    for i, item in enumerate(DEMO):
        contracts.append(
            Contract(
                contract_number=item["contract_number"],
                call_id=item["call_id"],
                contact_id=item["contact_id"],
                operator=item["operator"],
                contact_at=base + timedelta(minutes=i * 17),
                segment=item["segment"],
                transcript=item["transcript"],
            )
        )
    n = await repo.bulk_upsert(contracts)
    print(f"Vértice — {n} contratos demo inseridos.")


if __name__ == "__main__":
    asyncio.run(seed())
