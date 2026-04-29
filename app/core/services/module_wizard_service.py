"""Use case: wizard 'IA, me ajuda' para sugerir setup de novo módulo.

Recebe descrição livre em linguagem natural e devolve sugestão estruturada de:
- name (slug)
- endpoint_url
- description (refinada)
- config_params (dict)
- suggested_skill (str | None)

Estratégia: tenta LLM real; se falhar parse, cai em heurística determinística.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass

from app.core.ports.llm import LLMClient
from app.core.services.skill_service import SkillService


@dataclass
class WizardSuggestion:
    name: str
    endpoint_url: str
    description: str
    config_params: dict
    suggested_skill: str | None
    reasoning: str
    source: str  # "llm" | "heuristic"


_STOPWORDS = {
    "que", "para", "com", "uma", "isso", "esse", "essa", "este", "esta", "como",
    "quero", "preciso", "fazer", "criar", "gerar", "modulo", "módulo", "ia",
    "agente", "sistema", "novo", "nova", "tipo", "vai", "fica", "ser", "tem",
    "dos", "das", "uns", "umas", "tudo", "muito", "muita", "pelo", "pela",
}


def _slugify(text: str) -> str:
    """Converte texto livre em slug ASCII (a-z0-9_)."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii").lower()
    cleaned = re.sub(r"[^a-z0-9\s_]", " ", ascii_text)
    parts = [p for p in cleaned.split() if p and p not in _STOPWORDS]
    return "_".join(parts)[:40] or "novo_modulo"


def _heuristic_suggest(prompt: str, available_skills: list[str]) -> WizardSuggestion:
    """Fallback determinístico — extrai keywords e monta sugestão."""
    p = (prompt or "").strip()
    name = _slugify(p[:80] or "novo_modulo")
    # nome curto: pega no máximo 3 tokens significativos
    tokens = name.split("_")[:3]
    name = "_".join(tokens) or "novo_modulo"

    # heurísticas de config baseadas em palavras-chave
    cfg: dict = {"sanitization": True}
    low = p.lower()
    if any(w in low for w in ["score", "scoring", "pontuação", "pontuacao", "classific"]):
        cfg["threshold"] = 0.7
    if any(w in low for w in ["churn", "taxonom", "hierarqu", "hierarquia"]):
        cfg["auto_grow_taxonomy"] = True
    if any(w in low for w in ["audio", "áudio", "voz", "transcri"]):
        cfg["max_duration_seconds"] = 600
    if any(w in low for w in ["lote", "batch", "fila", "massa"]):
        cfg["batch_size"] = 50
    if any(w in low for w in ["sintetic", "sintétic", "synthetic", "fake", "geração", "geracao"]):
        cfg["seed_examples"] = 5
    if any(w in low for w in ["alerta", "monitor", "watch", "vigil"]):
        cfg["check_interval_seconds"] = 60

    # tenta achar skill compatível pelo nome
    suggested_skill = None
    name_tokens = set(name.split("_"))
    best_overlap = 0
    for s in available_skills:
        s_tokens = set(s.split("_"))
        overlap = len(name_tokens & s_tokens)
        if overlap > best_overlap:
            best_overlap = overlap
            suggested_skill = s
    if best_overlap == 0:
        suggested_skill = None

    description = p if len(p) > 20 else f"Módulo gerado a partir de: '{p}'"
    description = description[:200]

    return WizardSuggestion(
        name=name,
        endpoint_url=f"/api/{name}/v1/process",
        description=description,
        config_params=cfg,
        suggested_skill=suggested_skill,
        reasoning=(
            f"Sugestão heurística: slug derivado de '{p[:60]}', "
            f"params inferidos por keywords. "
            + (f"Skill '{suggested_skill}' encontrada por similaridade léxica." if suggested_skill else "Nenhuma skill compatível encontrada — crie uma nova.")
        ),
        source="heuristic",
    )


_SYSTEM_PROMPT = (
    "Você é um arquiteto de building blocks de IA. "
    "A partir da descrição do usuário, sugira a configuração inicial de um novo módulo "
    "que vai expor o Standard Module Contract em /v1/process. "
    "Responda EXCLUSIVAMENTE com um objeto JSON válido (sem markdown, sem texto antes ou depois) "
    "no seguinte schema:\n"
    "{\n"
    '  "name": "slug em snake_case, máximo 30 chars, só [a-z0-9_]",\n'
    '  "endpoint_url": "/api/{name}/v1/process",\n'
    '  "description": "1 frase curta em PT-BR descrevendo a função do módulo",\n'
    '  "config_params": {"chave_snake_case": valor},\n'
    '  "suggested_skill": "nome_skill ou null",\n'
    '  "reasoning": "1 frase explicando as escolhas"\n'
    "}\n"
    "Regras: name não pode conter espaços nem acentos. "
    "config_params deve refletir parâmetros operacionais reais (thresholds, batch sizes, flags), "
    "não placeholders. Se nenhuma skill da lista combinar, devolva null em suggested_skill."
)


class ModuleWizardService:

    def __init__(self, llms: dict[str, LLMClient], skills: SkillService):
        self.llms = llms
        self.skills = skills

    async def suggest(self, prompt: str, llm_preference: str | None = None) -> WizardSuggestion:
        available_skills = [s.name for s in self.skills.list_all()]
        if not prompt or not prompt.strip():
            return _heuristic_suggest("módulo genérico", available_skills)

        # escolhe LLM: preferência > primeiro disponível
        client = None
        if llm_preference and llm_preference in self.llms:
            client = self.llms[llm_preference]
        elif self.llms:
            client = next(iter(self.llms.values()))

        if not client:
            return _heuristic_suggest(prompt, available_skills)

        skill_list_str = ", ".join(available_skills) if available_skills else "(nenhuma)"
        user_prompt = (
            f"Descrição do módulo desejado:\n{prompt}\n\n"
            f"Skills já existentes na plataforma (escolha uma se for compatível): {skill_list_str}\n\n"
            "Devolva apenas o JSON."
        )

        try:
            resp = await client.complete(_SYSTEM_PROMPT, user_prompt, max_tokens=400, temperature=0.3)
            data = self._parse_json(resp.text)
            if data:
                # sanitização defensiva
                name = _slugify(data.get("name") or "")
                if not name:
                    raise ValueError("name inválido após sanitização")
                cfg = data.get("config_params") or {}
                if not isinstance(cfg, dict):
                    cfg = {}
                skill = data.get("suggested_skill")
                if skill and skill not in available_skills:
                    skill = None  # LLM alucinou; valida contra a lista real

                # se LLM devolveu sugestão fraca (params vazios OU skill ausente),
                # complementa com a heurística para não decepcionar o usuário
                heur = _heuristic_suggest(prompt, available_skills)
                if not cfg:
                    cfg = heur.config_params
                if not skill:
                    skill = heur.suggested_skill
                # se o name do LLM ficou degenerado tipo "novo_modulo", prefere a heurística
                if name in {"novo_modulo", "modulo", "exemplo", "teste"}:
                    name = heur.name

                description = (data.get("description") or heur.description)[:240]
                endpoint = data.get("endpoint_url") or f"/api/{name}/v1/process"
                reasoning = data.get("reasoning") or heur.reasoning

                return WizardSuggestion(
                    name=name,
                    endpoint_url=endpoint,
                    description=description,
                    config_params=cfg,
                    suggested_skill=skill,
                    reasoning=reasoning,
                    source="llm" if cfg != heur.config_params or skill != heur.suggested_skill else "llm+heuristic",
                )
        except Exception:  # noqa: BLE001
            # cai no fallback
            pass

        return _heuristic_suggest(prompt, available_skills)

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        if not text:
            return None
        # tenta direto
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # extrai bloco entre primeiro { e último }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def to_dict(s: WizardSuggestion) -> dict:
        return asdict(s)
