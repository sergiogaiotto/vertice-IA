"""Use case: Gestão Churn — taxonomia hierárquica + classificador."""

from __future__ import annotations

from uuid import UUID

from app.core.domain.entities import (
    ChurnClassification,
    ChurnNode,
    new_uuid,
)
from app.core.ports.guardrails import InputGuardrail, OutputGuardrail
from app.core.ports.repositories import ChurnRepository, FinOpsRepository
from app.core.services.finops_service import FinOpsService
from app.core.services.model_router import ModelRouter


class ChurnService:
    def __init__(
        self,
        churn: ChurnRepository,
        finops: FinOpsRepository,
        router: ModelRouter,
        input_guard: InputGuardrail,
        output_guard: OutputGuardrail,
    ):
        self.churn = churn
        self.finops = finops
        self.router = router
        self.input_guard = input_guard
        self.output_guard = output_guard

    # ---------- taxonomia ----------

    async def get_taxonomy(self) -> list[ChurnNode]:
        flat = await self.churn.get_taxonomy()
        return self._build_tree(flat)

    @staticmethod
    def _build_tree(nodes: list[ChurnNode]) -> list[ChurnNode]:
        by_id: dict[UUID, ChurnNode] = {n.id: n for n in nodes}
        roots: list[ChurnNode] = []
        for n in nodes:
            n.children = []
        for n in nodes:
            if n.parent_id and n.parent_id in by_id:
                by_id[n.parent_id].children.append(n)
            else:
                roots.append(n)
        return roots

    async def add_node(self, label: str, parent_id: UUID | None = None) -> ChurnNode:
        depth = 0
        if parent_id:
            flat = await self.churn.get_taxonomy()
            for n in flat:
                if n.id == parent_id:
                    depth = n.depth + 1
                    break
        node = ChurnNode(id=new_uuid(), label=label, parent_id=parent_id, depth=depth)
        return await self.churn.upsert_node(node)

    async def rename_node(self, node_id: UUID, new_label: str) -> ChurnNode:
        flat = await self.churn.get_taxonomy()
        for n in flat:
            if n.id == node_id:
                n.label = new_label
                return await self.churn.upsert_node(n)
        raise ValueError("nó não encontrado")

    async def delete_node(self, node_id: UUID) -> None:
        await self.churn.delete_node(node_id)

    # ---------- classificação ----------

    async def classify(self, contract_number: str, transcript: str, user_id=None) -> ChurnClassification:
        guard_in = self.input_guard.check(transcript)
        if not guard_in.ok:
            raise ValueError(f"Guardrail bloqueou: {guard_in.reason}")

        roots = await self.get_taxonomy()
        taxonomy_text = self._taxonomy_to_text(roots)

        system = (
            "Você é um classificador de motivos de churn para uma operadora de telecom. "
            "Receba uma transcrição de cliente e devolva o caminho hierárquico mais apropriado "
            "dentro da taxonomia fornecida. Se nenhum caminho se aplicar, sugira um novo nó "
            "indicando 'NOVO: <rótulo>'.\n"
            "Saída EXATA no formato JSON:\n"
            '{"path": ["nivel1", "nivel2", "..."], "confidence": 0.0-1.0, "rationale": "..."}'
        )
        user_msg = (
            f"Taxonomia atual:\n{taxonomy_text}\n\n"
            f"Transcrição:\n\"\"\"{guard_in.sanitized[:6000]}\"\"\""
        )

        llm = await self.router.complete(
            system_prompt=system,
            user_prompt=user_msg,
            output_type="INTENCAO",
        )

        guard_out = self.output_guard.check(llm.text, expected_format="JSON")
        text = guard_out.sanitized if guard_out.ok else llm.text

        path: list[str] = []
        confidence = 0.0
        rationale = ""
        try:
            import json
            parsed = json.loads(text)
            path = list(parsed.get("path", []))
            confidence = float(parsed.get("confidence", 0.0))
            rationale = str(parsed.get("rationale", ""))
        except Exception:
            # fallback: heurística rude para não quebrar o pipeline
            path = ["nao_classificado"]
            rationale = text[:200]

        classification = ChurnClassification(
            contract_number=contract_number,
            path=path,
            confidence=confidence,
            rationale=rationale,
        )
        await self.churn.save_classification(classification)

        await FinOpsService(self.finops).record(
            user_id=user_id,
            module_id=None,
            model_name=llm.model,
            tokens_input=llm.tokens_input,
            tokens_output=llm.tokens_output,
            cost_estimated=llm.cost_estimated,
            context_tag=f"churn/{contract_number}",
        )
        return classification

    @staticmethod
    def _taxonomy_to_text(roots: list[ChurnNode], depth: int = 0) -> str:
        lines: list[str] = []
        for n in roots:
            lines.append(f"{'  ' * depth}- {n.label}")
            if n.children:
                lines.append(ChurnService._taxonomy_to_text(n.children, depth + 1))
        return "\n".join(lines)
