"""Adaptador Azure OpenAI (default: deployment gpt-4o).

Substitui o cliente OpenAI direto. A API é praticamente idêntica via o
``AsyncAzureOpenAI`` do SDK ``openai>=2``; mudam apenas o ``api_key``,
``azure_endpoint`` e ``api_version`` na construção do client. O nome do
deployment (configurável via ``AZURE_OPENAI_DEPLOYMENT``) é passado como
``model=`` no ``chat.completions.create`` — é assim que a Azure roteia.
"""

from __future__ import annotations

from app.config import get_settings
from app.core.ports.llm import LLMClient, LLMResponse


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class AzureOpenAIClient(LLMClient):
    """Wrapper async para o endpoint Azure OpenAI Chat Completions.

    Defaults espelham a tarifa pública do gpt-4o (out 2025) — atualize as
    constantes ao mudar de deployment. Cache de input cobrado a ~50% no
    Azure OpenAI quando habilitado no recurso.
    """

    name = "gpt-4o"
    cost_per_1k_input = 0.0025
    cost_per_1k_output = 0.01
    cost_per_1k_cached_input = 0.00125

    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str | None = None,
        api_version: str | None = None,
        deployment: str | None = None,
    ):
        s = get_settings()
        self.api_key = api_key or s.azure_openai_api_key
        self.endpoint = endpoint or s.azure_openai_endpoint
        self.api_version = api_version or s.azure_openai_api_version
        # `name` é o "model" exposto no router/finops; em Azure equivale ao
        # nome do deployment configurado no recurso.
        self.name = deployment or s.azure_openai_deployment

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 800,
        temperature: float = 0.2,
        force_json: bool = False,
    ) -> LLMResponse:
        if not (self.api_key and self.endpoint):
            raise RuntimeError(
                "Azure OpenAI não configurado — defina AZURE_OPENAI_API_KEY e "
                "AZURE_OPENAI_ENDPOINT."
            )
        try:
            from openai import AsyncAzureOpenAI
        except ImportError as e:
            raise RuntimeError("openai>=2 não instalado.") from e

        client = AsyncAzureOpenAI(
            api_key=self.api_key,
            azure_endpoint=self.endpoint,
            api_version=self.api_version,
        )
        kwargs = dict(
            model=self.name,  # = deployment no Azure
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if force_json:
            # JSON mode também é suportado em Azure OpenAI (gpt-4o e cia).
            # Requisito: o system_prompt deve mencionar "JSON".
            kwargs["response_format"] = {"type": "json_object"}

        resp = await client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        ti = usage.prompt_tokens if usage else _approx_tokens(system_prompt + user_prompt)
        to = usage.completion_tokens if usage else _approx_tokens(text)
        cost = (ti / 1000) * self.cost_per_1k_input + (to / 1000) * self.cost_per_1k_output
        return LLMResponse(
            text=text,
            model=self.name,
            tokens_input=ti,
            tokens_output=to,
            cost_estimated=round(cost, 6),
        )
