"""HTTP-контракт ручек P2: me/quests, me/badges, wheel, wheel/spin (схема, доступ, заголовки)."""

from __future__ import annotations

from typing import Any

import pytest
from progression_service.api.public.schemas import MyBadges, MyQuests, SpinResult, WheelView

from gv_core.ids import uuid7
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py
ROUTES = [
    ("GET", "/v1/me/quests"),
    ("GET", "/v1/me/badges"),
    ("GET", "/v1/wheel"),
    ("POST", "/v1/wheel/spin"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_account_required_guest_forbidden(
    progression: Service, method: str, path: str
) -> None:
    await progression.publish_config(rewards=True)
    client = progression.client
    assert_problem(await client.request(method, path), 401, "auth.unauthenticated")
    guest = {"Authorization": f"Bearer {progression.issuer.issue_guest()}"}
    assert_problem(await client.request(method, path, headers=guest), 403, "auth.guest_forbidden")


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_responses_follow_schema_and_are_private(
    progression: Service, method: str, path: str
) -> None:
    await progression.publish_config(rewards=True)
    response = await progression.client.request(method, path, headers=progression.user(uuid7()))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    model = {
        "/v1/me/quests": MyQuests,
        "/v1/me/badges": MyBadges,
        "/v1/wheel": WheelView,
        "/v1/wheel/spin": SpinResult,
    }[path]
    parsed = model.model_validate(response.json())  # лишних/недостающих полей нет
    assert set(response.json()) == set(model.model_fields)
    assert parsed is not None


async def test_spin_rejects_any_body_with_422(progression: Service) -> None:
    await progression.publish_config(rewards=True)
    user = uuid7()
    for body in ({"sector_key": "verses_100"}, {"extra": True}, {"prize": {"kind": "xp"}}):
        assert_problem(
            await progression.spin(user, json=body), 422, "progression.spin_body_not_allowed"
        )
    assert await progression.rows("wheel_spins", user) == []
