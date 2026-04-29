"""Use case: gerenciamento centralizado de prompts (guardrail-system-guardrail)."""

from __future__ import annotations

import difflib
from uuid import UUID

from app.core.domain.entities import PromptBundle, new_uuid
from app.core.ports.repositories import PromptRepository


class PromptService:
    def __init__(self, prompts: PromptRepository):
        self.prompts = prompts

    async def list_all(self) -> list[PromptBundle]:
        return await self.prompts.list_all()

    async def list_for_module(self, module_name: str) -> list[PromptBundle]:
        return await self.prompts.list_for_module(module_name)

    async def get(self, prompt_id: UUID) -> PromptBundle | None:
        return await self.prompts.get(prompt_id)

    async def get_active(self, module_name: str, name: str) -> PromptBundle | None:
        return await self.prompts.get_active(module_name, name)

    async def save_new_version(
        self,
        name: str,
        input_guardrail: str,
        system_prompt: str,
        output_guardrail: str,
        module_names: list[str] | None = None,
    ) -> PromptBundle:
        existing = await self.prompts.list_all()
        same = [p for p in existing if p.name == name]
        next_version = (max((p.version for p in same), default=0)) + 1
        # se não foi passado module_names, herda da última versão (se houver)
        if module_names is None:
            module_names = same[0].module_names if same else []
        bundle = PromptBundle(
            id=new_uuid(),
            name=name,
            module_names=list(module_names),
            version=next_version,
            input_guardrail=input_guardrail,
            system_prompt=system_prompt,
            output_guardrail=output_guardrail,
            is_active=True,
        )
        return await self.prompts.save(bundle)

    async def promote(self, prompt_id: UUID) -> None:
        await self.prompts.promote(prompt_id)

    async def delete(self, prompt_id: UUID) -> None:
        await self.prompts.delete(prompt_id)

    async def set_modules(self, prompt_id: UUID, module_names: list[str]) -> None:
        """Atualiza N:N — propaga para todas as versões com mesmo nome."""
        prompt = await self.prompts.get(prompt_id)
        if not prompt:
            raise ValueError("prompt não encontrado")
        # propaga a todas as versões deste name (consistência da associação)
        all_versions = [p for p in await self.prompts.list_all() if p.name == prompt.name]
        for p in all_versions:
            await self.prompts.set_modules(p.id, module_names)

    async def diff(self, a_id: UUID, b_id: UUID) -> dict:
        a = await self.get(a_id)
        b = await self.get(b_id)
        if not a or not b:
            raise ValueError("um dos prompts não foi encontrado")

        def _d(label: str, x: str, y: str) -> list[str]:
            return list(difflib.unified_diff(
                (x or "").splitlines(),
                (y or "").splitlines(),
                lineterm="",
                fromfile=f"v{a.version} · {label}",
                tofile=f"v{b.version} · {label}",
                n=2,
            ))

        return {
            "from": {"id": str(a.id), "version": a.version, "name": a.name},
            "to": {"id": str(b.id), "version": b.version, "name": b.name},
            "input_guardrail": _d("guardrail entrada", a.input_guardrail, b.input_guardrail),
            "system_prompt": _d("system prompt", a.system_prompt, b.system_prompt),
            "output_guardrail": _d("guardrail saída", a.output_guardrail, b.output_guardrail),
        }
