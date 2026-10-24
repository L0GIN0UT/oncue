"""Трейсинг: query запроса (одноразовые токены) не попадает в атрибуты span."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from gv_observability import instrument_app

pytestmark = pytest.mark.unit

SECRET = "tok-9f8e7d6c5b4a39281706f5e4d3c2b1a0"


async def test_span_attributes_do_not_contain_query_token() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = FastAPI()

    @app.post("/v1/unsubscribe/one-click")
    async def one_click() -> None:
        return None

    instrument_app(app, tracer_provider=provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(f"/v1/unsubscribe/one-click?token={SECRET}")
    assert response.status_code == 200

    spans = exporter.get_finished_spans()
    assert spans, "span сервера не создан"
    for span in spans:
        for key, value in (span.attributes or {}).items():
            assert SECRET not in str(value), key
            assert "token=" not in str(value), key
    server = next(s for s in spans if (s.attributes or {}).get("http.target"))
    assert (server.attributes or {})["http.target"] == "/v1/unsubscribe/one-click"
