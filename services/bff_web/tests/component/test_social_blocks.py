"""Контракт bff 5c (шаг 0a): необязательные блоки social не ломают экраны 5a-5b."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from bff_web_service.main import DEFINITION
from bff_web_service.settings import Settings

from gv_service import Infrastructure, create_app
from gv_testkit import TestTokenIssuer, service_client

pytestmark = pytest.mark.component


@pytest.fixture
async def schemas() -> AsyncIterator[dict[str, Any]]:
    infra = await Infrastructure.open(Settings(environment="ci"), DEFINITION.tables)
    try:
        app = create_app(DEFINITION, infra, jwt_verifier=TestTokenIssuer().verifier())
        async with service_client(app) as http:
            yield await _schemas(http)
    finally:
        await infra.close()


async def _schemas(http: httpx.AsyncClient) -> dict[str, Any]:
    components: dict[str, Any] = (await http.get("/openapi.json")).json()["components"]
    return components["schemas"]


@pytest.mark.parametrize(
    ("model", "field"),
    [
        ("PlayerBlocks", "social"),
        ("OnlineBlocks", "friends"),
        ("CollectionBlocks", "comments"),
        ("ShellPage", "sanctions"),
        ("PlayerPage", "restricted"),
        ("MatchPlayerCard", "can_report"),
    ],
)
def test_social_fields_are_optional(schemas: dict[str, Any], model: str, field: str) -> None:
    """Поля 5c необязательны: старые потребители и тесты 5a-5b остаются совместимыми."""
    assert field in schemas[model]["properties"]
    assert field not in schemas[model].get("required", [])


def test_friends_card_exposes_only_status_not_presence_details(schemas: dict[str, Any]) -> None:
    friend = schemas["FriendCard"]["properties"]
    assert set(friend) == {"user_id", "nickname", "status"}
    assert set(friend["status"]["enum"]) == {"online", "in_match", "offline"}


def test_sanction_card_has_no_moderator_data(schemas: dict[str, Any]) -> None:
    assert set(schemas["SanctionCard"]["properties"]) == {"kind", "expires_at", "reason"}
