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
    """Forward-only para LangFuse / MLflow / OTel.

    Stateless do lado da aplicação: cada chamada de ``trace``/``event`` é
    repassada aos backends configurados e descartada localmente. Não há
    buffer in-memory — antes existia, mas nenhum caller consumia, e isso
    seria uma armadilha em deploy multi-worker (cada worker teria seu
    próprio buffer, fora-de-sync).
    """

    def __init__(self):
        self._langfuse = self._make_langfuse()
        self._mlflow = self._make_mlflow()
        self._otel = self._make_otel()

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
        if self._langfuse:
            try:
                # SDK v3 (>=3.0): a API foi reescrita sobre OpenTelemetry e o
                # método `.trace(name=...)` antigo foi removido. O padrão atual
                # é context manager via `start_as_current_observation` (ou
                # `start_as_current_span`). `as_type="span"` é o equivalente
                # genérico ao trace v2; use `as_type="generation"` quando o
                # caller for uma chamada LLM com tokens/custos a registrar.
                with self._langfuse.start_as_current_observation(
                    as_type="span",
                    name=name,
                    input=input_data,
                ) as span:
                    span.update(output=output_data, metadata=metadata or {})
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
