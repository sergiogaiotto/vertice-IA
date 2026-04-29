"""Tracer composto que distribui eventos para LangFuse, MLflow e OTel.

Cada backend é opt-in via variáveis de ambiente. Se nenhum estiver configurado,
o tracer faz log local em memória — útil para dev.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.core.ports.observability import Tracer

settings = get_settings()
logger = logging.getLogger("vertice.tracer")


class CompositeTracer(Tracer):
    def __init__(self):
        self._langfuse = self._make_langfuse()
        self._mlflow = self._make_mlflow()
        self._otel = self._make_otel()
        self._buffer: list[dict] = []

    def _make_langfuse(self):
        if not (settings.langfuse_public_key and settings.langfuse_secret_key):
            return None
        try:
            from langfuse import Langfuse
            return Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("LangFuse não inicializado: %s", e)
            return None

    def _make_mlflow(self):
        if not settings.mlflow_tracking_uri:
            return None
        try:
            import mlflow
            mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
            return mlflow
        except Exception as e:  # noqa: BLE001
            logger.warning("MLflow não inicializado: %s", e)
            return None

    def _make_otel(self):
        if not settings.otel_exporter_otlp_endpoint:
            return None
        try:
            from opentelemetry import trace
            return trace.get_tracer(settings.otel_service_name)
        except Exception as e:  # noqa: BLE001
            logger.warning("OTel não inicializado: %s", e)
            return None

    def trace(self, name: str, input_data: Any, output_data: Any, metadata: dict | None = None) -> None:
        record = {"name": name, "input": input_data, "output": output_data, "meta": metadata or {}}
        self._buffer.append(record)
        if len(self._buffer) > 500:
            self._buffer = self._buffer[-500:]

        if self._langfuse:
            try:
                self._langfuse.trace(name=name, input=input_data, output=output_data, metadata=metadata)
            except Exception as e:  # noqa: BLE001
                logger.debug("LangFuse trace falhou: %s", e)

        if self._mlflow:
            try:
                self._mlflow.log_dict({"input": input_data, "output": output_data, "meta": metadata}, f"{name}.json")
            except Exception as e:  # noqa: BLE001
                logger.debug("MLflow log falhou: %s", e)

        if self._otel:
            try:
                with self._otel.start_as_current_span(name) as span:
                    if metadata:
                        for k, v in metadata.items():
                            span.set_attribute(str(k), str(v))
            except Exception as e:  # noqa: BLE001
                logger.debug("OTel span falhou: %s", e)

    def event(self, name: str, payload: dict) -> None:
        self.trace(name=name, input_data=None, output_data=payload, metadata={"kind": "event"})

    def buffer(self) -> list[dict]:
        return list(self._buffer)
