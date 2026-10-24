"""Заглушки bff 5d (шаг 0a, ADR 0054): `/pages/events/{key}` - контракт, до E3 - 501."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from bff_web_service.main import DEFINITION
from bff_web_service.settings import Settings

from gv_service import Infrastructure, create_app
from gv_testkit import TestTokenIssuer, assert_problem, service_client

pytestmark = pytest.mark.component


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    infra = await Infrastructure.open(Settings(environment="ci"), DEFINITION.tables)
    try:
        app = create_app(DEFINITION, infra, jwt_verifier=TestTokenIssuer().verifier())
        async with service_client(app) as http:
            yield http
    finally:
        await infra.close()


async def test_event_page_answers_501_until_stream_e3(client: httpx.AsyncClient) -> None:
    assert_problem(await client.get("/v1/pages/events/autumn"), 501, "common.not_implemented")


async def test_openapi_describes_event_blocks_as_optional(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()
    assert "/v1/pages/events/{event_key}" in spec["paths"]
    schemas = spec["components"]["schemas"]
    for name, field in (
        ("ShellPage", "event"),
        ("HomeBlocks", "event"),
        ("ProfileBlocks", "events"),
    ):
        assert field in schemas[name]["properties"]
        assert field not in schemas[name].get("required", [])  # прежние клиенты совместимы
    assert set(schemas["EventPageView"]["properties"]) >= {"theme", "finds", "theme_key"}
