"""Guardrail de saída — valida formato esperado e remove vazamentos."""

from __future__ import annotations

import json
import re

from app.core.ports.guardrails import GuardrailResult, OutputGuardrail


_LEAK_PATTERNS = [
    r"my\s+system\s+prompt\s+is",
    r"meu\s+prompt\s+do\s+sistema\s+[ée]",
    r"as\s+an?\s+(AI|LLM|language\s+model)",
    r"como\s+um[a]?\s+(IA|modelo\s+de\s+linguagem)",
]


class DefaultOutputGuardrail(OutputGuardrail):
    """Valida saída por formato esperado e remove auto-revelações triviais."""

    def check(self, text: str, expected_format: str = "", policy: str = "") -> GuardrailResult:
        flags: list[str] = []
        sanitized = (text or "").strip()

        for pat in _LEAK_PATTERNS:
            if re.search(pat, sanitized, re.IGNORECASE):
                flags.append("SELF_DISCLOSURE")
                sanitized = re.sub(pat, "[REMOVIDO]", sanitized, flags=re.IGNORECASE)

        ef = (expected_format or "").upper()

        if ef in {"UMA_PALAVRA", "ONE_WORD"}:
            words = re.findall(r"\w+", sanitized)
            if not words:
                return GuardrailResult(ok=False, sanitized="", reason="vazio", flags=flags)
            sanitized = words[0].lower()
            return GuardrailResult(ok=True, sanitized=sanitized, flags=flags)

        if ef == "SCORE":
            m = re.search(r"\b(100|[0-9]{1,2})\b", sanitized)
            if not m:
                return GuardrailResult(ok=False, sanitized="", reason="score não encontrado", flags=flags)
            return GuardrailResult(ok=True, sanitized=m.group(1), flags=flags)

        if ef == "TERMOS":
            terms = [t.strip() for t in re.split(r"[,;]\s*", sanitized) if t.strip()]
            terms = terms[:8]
            return GuardrailResult(ok=True, sanitized=", ".join(terms), flags=flags)

        if ef == "JSON":
            # tenta extrair primeiro objeto JSON válido
            match = re.search(r"\{[\s\S]*\}", sanitized)
            if not match:
                return GuardrailResult(ok=False, sanitized=sanitized, reason="JSON não encontrado", flags=flags)
            try:
                parsed = json.loads(match.group(0))
                return GuardrailResult(ok=True, sanitized=json.dumps(parsed, ensure_ascii=False), flags=flags)
            except json.JSONDecodeError:
                return GuardrailResult(ok=False, sanitized=match.group(0), reason="JSON inválido", flags=flags)

        # Texto livre — cada formato tem cap diferente. LIVRE = sem cap.
        # Se o texto excede o cap, cortamos no último espaço e anexamos `…`
        # para sinalizar a quebra ao usuário (caractere unicode literal).
        TEXT_CAPS = {
            "INTENCAO":  600,    # frase curta sobre intenção
            "RESUMO":    600,    # resumo curto
            "SUMARIO":  1500,    # sumário padrão
            "ANALISE":  4000,    # análise estruturada média
            "RELATORIO": 8000,   # relatório longo
            "LIVRE":     None,   # sem corte — texto total preservado
        }
        if ef in TEXT_CAPS:
            max_chars = TEXT_CAPS[ef]
            if max_chars is not None and len(sanitized) > max_chars:
                sanitized = sanitized[:max_chars].rsplit(" ", 1)[0] + "…"
            return GuardrailResult(ok=True, sanitized=sanitized, flags=flags)

        return GuardrailResult(ok=True, sanitized=sanitized, flags=flags)
