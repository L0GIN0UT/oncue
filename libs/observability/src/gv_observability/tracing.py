"""Трейсинг OpenTelemetry.

Если ``otlp_endpoint`` не задан, трейсинг выключен (no-op) — локально и в тестах ничего
настраивать не нужно. В окружениях с коллектором трейсы уходят по OTLP/HTTP.
Контекст передаётся заголовком W3C ``traceparent``.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Span


def configure_tracing(
    *, service: str, env: str, otlp_endpoint: str | None, sample_ratio: float = 1.0
) -> bool:
    """Настроить глобальный TracerProvider.

    Returns:
        ``True``, если трейсинг включён.
    """
    if not otlp_endpoint:
        return False
    provider = TracerProvider(
        resource=Resource.create({"service.name": service, "deployment.environment": env}),
        sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    return True


def _redact_query(span: Span, scope: dict[str, Any]) -> None:
    """Убрать query из атрибутов span: в ней бывают одноразовые токены (отписка, ссылки из писем).

    Инструментация пишет ``http.target``/``http.url``/``url.query`` с query ДО вызова хука, поэтому
    перезаписываем значения: в трейс попадает только путь.
    """
    if not span.is_recording():
        return
    path = str(scope.get("path", ""))
    span.set_attribute("http.target", path)
    span.set_attribute("http.url", path)
    span.set_attribute("url.query", "")


def instrument_app(app: FastAPI, *, tracer_provider: TracerProvider | None = None) -> None:
    """Включить автоинструментацию FastAPI (служебные ручки исключены, query в span не пишется)."""
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="health/live,health/ready,metrics",
        server_request_hook=_redact_query,
        tracer_provider=tracer_provider,
    )
