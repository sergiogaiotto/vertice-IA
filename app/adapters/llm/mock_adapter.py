"""Adaptador LLM em modo mock — usado quando não há API key configurada.

Permite que a plataforma seja navegável e testável sem chamadas externas.
"""

from __future__ import annotations

import hashlib
import random

from app.core.ports.llm import LLMClient, LLMResponse


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class MockLLMClient(LLMClient):
    """Devolve respostas determinísticas a partir do hash do prompt."""

    def __init__(
        self,
        name: str = "mock",
        cost_per_1k_input: float = 0.0,
        cost_per_1k_output: float = 0.0,
        cost_per_1k_cached_input: float | None = None,
    ):
        self.name = name
        self.cost_per_1k_input = cost_per_1k_input
        self.cost_per_1k_output = cost_per_1k_output
        # default: cached = 25% de input (espelha OpenAI). Se cliente passou
        # explicitamente, respeita.
        self.cost_per_1k_cached_input = (
            cost_per_1k_cached_input
            if cost_per_1k_cached_input is not None
            else cost_per_1k_input * 0.25
        )

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 800,
        temperature: float = 0.2,
        force_json: bool = False,
    ) -> LLMResponse:
        seed = int(hashlib.md5((system_prompt + user_prompt).encode()).hexdigest(), 16) % (2**32)
        rnd = random.Random(seed)

        # gera resposta condizente com o tipo de saída sugerido em system_prompt
        sp = system_prompt.lower()
        if "uma palavra" in sp:
            text = rnd.choice(["cancelamento", "reclamacao", "duvida", "elogio", "renegociacao"])
        elif "número inteiro" in sp or "0 e 100" in sp:
            text = str(rnd.randint(40, 95))
        elif "lista de até 8 termos" in sp or "termos separados por vírgula" in sp:
            text = ", ".join(rnd.sample(
                ["cobranca", "roaming", "portabilidade", "atendimento", "sinal", "fatura", "estorno", "plano"],
                k=5,
            ))
        elif "json" in sp:
            text = (
                '{"path": ["Preço", "Plano caro"], "confidence": 0.78, '
                '"rationale": "Cliente menciona explicitamente que outra operadora oferece mais barato."}'
            )
        elif "sumário" in sp or "sumario" in sp:
            text = (
                "Cliente reporta cobrança duplicada do serviço de roaming após viagem internacional. "
                "Operadora ofereceu estorno em dois ciclos; cliente recusou e ameaçou portabilidade. "
                "Atendimento N1 escalou para N2 sem resolução em primeira instância."
            )
        elif "resumo" in sp:
            text = "Cobrança duplicada de roaming; cliente recusa estorno e ameaça portabilidade."
        else:
            text = "Análise concluída em modo mock. Configure as API keys em .env para ativar os modelos reais."

        ti = _approx_tokens(system_prompt + user_prompt)
        to = _approx_tokens(text)
        cost = (ti / 1000) * self.cost_per_1k_input + (to / 1000) * self.cost_per_1k_output
        return LLMResponse(
            text=text,
            model=self.name,
            tokens_input=ti,
            tokens_output=to,
            cost_estimated=round(cost, 6),
            raw={"mock": True},
        )
