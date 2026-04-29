"""Guardrail de entrada — sanitização e proteção contra prompt injection."""

from __future__ import annotations

import re

from app.config import get_settings
from app.core.ports.guardrails import GuardrailResult, InputGuardrail

settings = get_settings()

# Padrões clássicos de injection (case-insensitive)
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"esquec[ae]\s+(todas\s+)?(as\s+)?instru[cç][oõ]es",
    r"disregard\s+(all\s+)?(prior|previous)\s+(instructions|rules)",
    r"system\s*[:>]\s*",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"###\s*system",
    r"jailbreak",
    r"DAN\s+mode",
    r"act\s+as\s+if\s+you\s+have\s+no",
    r"reveal\s+(your\s+)?(system\s+)?prompt",
    r"mostre\s+seu\s+prompt\s+(do\s+)?sistema",
]

# PII patterns (Brasil)
_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_CNPJ = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_PHONE = re.compile(r"\b(?:\+?55\s?)?(?:\(?\d{2}\)?\s?)?9?\d{4}-?\d{4}\b")
_CARD = re.compile(r"\b(?:\d{4}[\s-]?){3}\d{4}\b")


class DefaultInputGuardrail(InputGuardrail):
    """Guardrail de entrada padrão.

    Cadeia: limite de tamanho → detecção de injection → redação opcional de PII.
    """

    def __init__(
        self,
        max_chars: int | None = None,
        block_injection: bool | None = None,
        redact_pii: bool | None = None,
    ):
        self.max_chars = max_chars or settings.guardrail_input_max_chars
        self.block_injection = settings.guardrail_injection_block if block_injection is None else block_injection
        self.redact_pii = settings.guardrail_pii_redact if redact_pii is None else redact_pii

    def check(self, text: str, policy: str = "") -> GuardrailResult:
        flags: list[str] = []
        if not isinstance(text, str):
            text = str(text or "")

        if len(text) > self.max_chars:
            return GuardrailResult(
                ok=False,
                sanitized=text[: self.max_chars],
                reason=f"input excede {self.max_chars} caracteres",
                flags=["TOO_LONG"],
            )

        for pat in _INJECTION_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                flags.append("INJECTION_PATTERN")
                if self.block_injection:
                    return GuardrailResult(
                        ok=False,
                        sanitized="",
                        reason="padrão de prompt injection detectado",
                        flags=flags,
                    )

        sanitized = text
        if self.redact_pii:
            for pat, label in (
                (_CARD, "[CARTAO_REDIGIDO]"),
                (_CPF, "[CPF_REDIGIDO]"),
                (_CNPJ, "[CNPJ_REDIGIDO]"),
                (_EMAIL, "[EMAIL_REDIGIDO]"),
                (_PHONE, "[TELEFONE_REDIGIDO]"),
            ):
                if pat.search(sanitized):
                    flags.append(label.strip("[]"))
                    sanitized = pat.sub(label, sanitized)

        return GuardrailResult(ok=True, sanitized=sanitized, reason="", flags=flags)
