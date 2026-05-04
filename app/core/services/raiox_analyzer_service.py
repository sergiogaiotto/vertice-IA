"""Use case: Análise Inteligente de um dashboard do Raio X Cliente.

Para cada chart do board:
  1) Resolve a query (tabela + agg + filtros) e amostra os dados
  2) Lê opcional SKILL.md indicada em chart.skill_path para enquadrar o LLM
  3) Pede ao LLM uma análise focada (insights, números chave)

Depois, faz uma síntese conjunta com 4 seções:
  - Correlações
  - Padrões
  - Riscos
  - Oportunidades

A síntese usa todos os títulos + amostras dos charts como contexto.
Tudo é registrado no finops_ledger via FinOpsService (feature='raiox',
agent='raiox_analyzer').
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.domain.entities import RaioXChart
from app.core.services.finops_service import FinOpsService
from app.core.services.model_router import ModelRouter
from app.core.services.raiox_service import RaioXService
from app.core.services.skill_service import SkillService


@dataclass
class ChartAnalysis:
    chart_id: str
    title: str
    chart_type: str
    skill: str = ""
    rows_returned: int = 0
    analysis: str = ""
    error: str = ""
    model_used: str = ""
    tokens_input: int = 0
    tokens_output: int = 0
    cost_estimated: float = 0.0


@dataclass
class BoardAnalysis:
    board_id: str
    board_name: str
    per_chart: list[ChartAnalysis] = field(default_factory=list)
    correlations: str = ""
    patterns: str = ""
    risks: str = ""
    opportunities: str = ""
    total_cost: float = 0.0
    total_tokens_input: int = 0
    total_tokens_output: int = 0
    model_used: str = ""


class RaioXAnalyzerService:
    """Orquestra a análise inteligente de um dashboard."""

    def __init__(
        self,
        raiox: RaioXService,
        router: ModelRouter,
        finops: FinOpsService | None = None,
        skills: SkillService | None = None,
    ):
        self._raiox = raiox
        self._router = router
        self._finops = finops
        self._skills = skills or SkillService()

    # ------------------------------------------------------------------

    def _read_skill(self, skill_path: str) -> str:
        """Lê o conteúdo de um SKILL.md a partir do caminho relativo do projeto.
        Falha silenciosamente devolvendo '' se o arquivo não existir."""
        if not skill_path:
            return ""
        try:
            p = Path(skill_path)
            if not p.is_absolute():
                # caminho relativo à raiz do projeto (mesmo padrão usado pelo resto)
                p = Path.cwd() / skill_path
            if p.exists() and p.is_file():
                txt = p.read_text(encoding="utf-8", errors="ignore")
                # corta para não estourar contexto
                return txt[:4000]
        except Exception:
            pass
        return ""

    def _series_summary(self, series: dict[str, Any], limit: int = 12) -> str:
        """Compacta {labels, values} num bloco de texto pequeno para o prompt."""
        labels = series.get("labels", [])[:limit]
        values = series.get("values", [])[:limit]
        if not labels:
            return "(sem dados)"
        rows = []
        for lbl, val in zip(labels, values):
            try:
                v = round(float(val), 2)
            except Exception:
                v = val
            rows.append(f"  - {lbl}: {v}")
        head = f"top {len(rows)} de {series.get('rows_returned', len(labels))} (total tabela: {series.get('total_rows', '?')}):"
        return head + "\n" + "\n".join(rows)

    async def _analyze_one_chart(
        self,
        chart: RaioXChart,
        user_id: str | None = None,
    ) -> ChartAnalysis:
        out = ChartAnalysis(
            chart_id=str(chart.id),
            title=chart.title or f"{chart.chart_type} · {chart.query_spec.get('table', '?')}",
            chart_type=chart.chart_type,
            skill=chart.skill_path or "",
        )
        try:
            series = await self._raiox.build_series(chart.query_spec)
            out.rows_returned = int(series.get("rows_returned", 0))
            data_block = self._series_summary(series)
        except Exception as e:
            out.error = f"falha ao consultar dados: {e}"
            return out

        skill_text = self._read_skill(chart.skill_path)
        skill_section = ""
        if skill_text:
            skill_section = f"\n\n## SKILL aplicada (foco da análise)\n{skill_text}\n"

        system_prompt = (
            "Você é um analista de dados experiente do Vértice. "
            "Recebe a especificação de UM gráfico e uma amostra dos dados. "
            "Devolva uma análise objetiva em PT-BR com **3 a 6 bullets** em markdown:\n"
            "- destaque o que mais chama atenção (top/bottom, outliers)\n"
            "- mencione números concretos\n"
            "- evite jargão; vá direto ao insight de negócio\n"
            "Nada de saudações, conclusões obvias ou pedidos de mais contexto." + skill_section
        )
        qs = chart.query_spec
        user_prompt = (
            f"## Gráfico\n"
            f"- Título: {out.title}\n"
            f"- Tipo: {chart.chart_type}\n"
            f"- Tabela: {qs.get('table')}\n"
            f"- Label (X): {qs.get('label_column')}\n"
            f"- Valor (Y): {qs.get('value_label') or qs.get('value_column') or '(count)'}\n"
            f"- Agregação: {qs.get('aggregate')}\n"
            f"- Filtros: {qs.get('filters') or 'nenhum'}\n\n"
            f"## Dados\n{data_block}\n\n"
            "Análise:"
        )

        try:
            resp = await self._router.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=600,
                temperature=0.3,
            )
            out.analysis = (resp.text or "").strip()
            out.model_used = resp.model
            out.tokens_input = resp.tokens_input
            out.tokens_output = resp.tokens_output
            out.cost_estimated = resp.cost_estimated
            await self._record_finops(resp, user_id, agent="raiox_analyzer", flow="per_chart")
        except Exception as e:
            out.error = f"falha LLM: {e}"
        return out

    async def _synthesis(
        self,
        board_name: str,
        chart_analyses: list[ChartAnalysis],
        user_id: str | None = None,
    ) -> tuple[dict[str, str], str, int, int, float]:
        """Pede ao LLM as 4 seções de síntese (correlações, padrões, riscos, oportunidades)."""
        if not chart_analyses:
            return ({"correlations": "", "patterns": "", "risks": "", "opportunities": ""}, "", 0, 0, 0.0)

        charts_block = "\n\n".join(
            f"### {ca.title} ({ca.chart_type})\n{ca.analysis or ca.error or '(sem análise)'}"
            for ca in chart_analyses
        )

        system_prompt = (
            "Você é um analista sênior. Recebe N análises de gráficos de um mesmo dashboard. "
            "Produza uma síntese conjunta em JSON estrito com 4 chaves, cada uma contendo "
            "markdown em PT-BR (2-4 bullets cada):\n"
            '{\n'
            '  "correlations": "...",\n'
            '  "patterns": "...",\n'
            '  "risks": "...",\n'
            '  "opportunities": "..."\n'
            '}\n'
            "Seja específico, cite números/labels que apareceram. Nada de prefácios."
        )
        user_prompt = (
            f"# Dashboard: {board_name}\n\n"
            f"## Análises individuais\n{charts_block}\n\n"
            "Devolva APENAS o JSON."
        )

        try:
            resp = await self._router.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=900,
                temperature=0.3,
                force_json=True,
            )
            text = (resp.text or "").strip()
            # extração JSON robusta
            data: dict[str, str] = {}
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # tenta achar primeiro { e último }
                first, last = text.find("{"), text.rfind("}")
                if first >= 0 and last > first:
                    try:
                        data = json.loads(text[first : last + 1])
                    except json.JSONDecodeError:
                        data = {}
            await self._record_finops(resp, user_id, agent="raiox_analyzer", flow="synthesis")
            return (
                {
                    "correlations": str(data.get("correlations", "") or ""),
                    "patterns": str(data.get("patterns", "") or ""),
                    "risks": str(data.get("risks", "") or ""),
                    "opportunities": str(data.get("opportunities", "") or ""),
                },
                resp.model,
                resp.tokens_input,
                resp.tokens_output,
                resp.cost_estimated,
            )
        except Exception:
            return (
                {"correlations": "", "patterns": "", "risks": "", "opportunities": ""},
                "",
                0,
                0,
                0.0,
            )

    async def _record_finops(self, resp, user_id: str | None, agent: str, flow: str) -> None:
        if not self._finops:
            return
        try:
            from app.core.domain.entities import FinOpsEntry
            await self._finops.repo.append(
                FinOpsEntry(
                    id=None,
                    user_id=UUID(user_id) if user_id else None,
                    module_id=None,
                    model_name=resp.model,
                    tokens_input=resp.tokens_input,
                    tokens_output=resp.tokens_output,
                    cost_estimated=resp.cost_estimated,
                    context_tag="raiox/analyzer",
                    domain="raiox",
                    agent=agent,
                    flow=flow,
                )
            )
        except Exception:
            pass

    # ------------------------------------------------------------------

    async def analyze_board(
        self,
        board_id: UUID,
        user_id: str | None = None,
    ) -> BoardAnalysis:
        board = await self._raiox.get_board(board_id)
        if not board:
            raise ValueError("board não encontrado")
        charts = await self._raiox.list_charts(board_id)

        out = BoardAnalysis(board_id=str(board.id), board_name=board.name)
        for c in charts:
            ca = await self._analyze_one_chart(c, user_id=user_id)
            out.per_chart.append(ca)
            out.total_cost += ca.cost_estimated
            out.total_tokens_input += ca.tokens_input
            out.total_tokens_output += ca.tokens_output
            if ca.model_used and not out.model_used:
                out.model_used = ca.model_used

        synthesis, syn_model, syn_in, syn_out, syn_cost = await self._synthesis(
            board.name, out.per_chart, user_id=user_id,
        )
        out.correlations = synthesis["correlations"]
        out.patterns = synthesis["patterns"]
        out.risks = synthesis["risks"]
        out.opportunities = synthesis["opportunities"]
        out.total_cost += syn_cost
        out.total_tokens_input += syn_in
        out.total_tokens_output += syn_out
        if syn_model and not out.model_used:
            out.model_used = syn_model
        return out
