"""Заглушки bff 5b (шаг 0a, ADR 0049): `/pages/shop` и `/pages/shell` - контракт, до S5 - 501."""

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


@pytest.mark.parametrize("path", ["/v1/pages/shop", "/v1/pages/shell"])
async def test_stub_pages_answer_501_until_stream_s5(client: httpx.AsyncClient, path: str) -> None:
    assert_problem(await client.get(path), 501, "common.not_implemented")


async def test_openapi_describes_shop_shell_and_optional_profile_blocks(
    client: httpx.AsyncClient,
) -> None:
    spec = (await client.get("/openapi.json")).json()
    assert "/v1/pages/shop" in spec["paths"]
    assert "/v1/pages/shell" in spec["paths"]
    schemas = spec["components"]["schemas"]
    theme = schemas["ThemeCard"]["properties"]["key"]
    assert theme["pattern"] == "^[a-z][a-z0-9_]*$"  # только ключ, значений токенов в API нет
    assert set(schemas["ThemeCard"]["properties"]) == {"key"}
    profile = schemas["ProfileBlocks"]
    for block in ("appearance", "inventory", "avatar"):
        assert block in profile["properties"]
        assert block not in profile["required"]  # профиль 5a остаётся совместимым
