"""Контракты шага 0a этапа 2: гостевая сессия объявлена (501 до потока C).

Сервисные токены (0b) — ``test_service_tokens.py``.
"""

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
