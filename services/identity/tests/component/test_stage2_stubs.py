"""Контракты шага 0a этапа 2: гостевая сессия и сервисные токены объявлены (501 до 0b/C)."""

from __future__ import annotations

from typing import Any

import pytest

from gv_testkit import assert_problem

pytestmark = pytest.mark.component

ORIGIN = "https://gv.test"


async def test_guest_session_requires_same_origin_then_stub(service: Any) -> None:
    foreign = await service.client.post("/v1/auth/guest", headers={"Origin": "https://evil.test"})
    assert foreign.status_code == 403
    response = await service.client.post("/v1/auth/guest", headers={"Origin": ORIGIN})
    assert_problem(response, 501, "common.not_implemented")


async def test_service_tokens_contract(service: Any) -> None:
    body = {"client_id": "game", "client_secret": "s", "audience": "media_pipeline"}
    response = await service.client.post("/internal/v1/service-tokens", json=body)
    assert_problem(response, 501, "common.not_implemented")
    bad = await service.client.post(
        "/internal/v1/service-tokens", json={**body, "extra": 1, "audience": "Media"}
    )
    assert bad.status_code == 422
