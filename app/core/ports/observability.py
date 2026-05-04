"""Port de observabilidade."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Tracer(ABC):
    @abstractmethod
    def trace(
        self,
        name: str,
        input_data: Any,
        output_data: Any,
        metadata: dict | None = None,
    ) -> None: ...

    @abstractmethod
    def event(self, name: str, payload: dict) -> None: ...
