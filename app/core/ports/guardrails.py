"""Ports para guardrails de entrada e saída."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class GuardrailResult:
    ok: bool
    sanitized: str
    reason: str = ""
    flags: list[str] | None = None


class InputGuardrail(ABC):
    @abstractmethod
    def check(self, text: str, policy: str = "") -> GuardrailResult: ...


class OutputGuardrail(ABC):
    @abstractmethod
    def check(self, text: str, expected_format: str = "", policy: str = "") -> GuardrailResult: ...
