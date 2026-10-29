"""Публичный счётчик «игроков онлайн»: ZSET в Redis, гости не считаются, окно, лимит."""

from __future__ import annotations

from typing import Any

import pytest
from social_service.application.online_count import OnlineCounter
from social_service.infrastructure.adapters import PRESENCE_ONLINE_KEY

from gv_core.ids import uuid7
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

Social = Any  # фикстура `social` описана в conftest.py
URL = "/v1/presence/online-count"


async def beat(social: Social, user_id: Any) -> None:
    response = await social.client.post("/v1/me/presence", headers=social.headers(user_id))
    assert response.status_code == 204


def fresh_counter(social: Social, *, cache_s: float = 0.0, per_minute: int = 1000) -> None:
    """Подменить счётчик контейнера (кэш и лимит берутся из настроек, в тесте - свои)."""
    c = social.container
    c.__dict__["online_counter"] = OnlineCounter(
        c.presence, c.limiter, social.clock, cache_s=cache_s, per_minute=per_minute
    )


async def test_public_count_without_token_counts_distinct_accounts(social: Social) -> None:
    await social.publish_config()
    fresh_counter(social)
    first, second = uuid7(), uuid7()
    await beat(social, first)
    await beat(social, second)
    # повторный heartbeat того же игрока не удваивает счёт (в пределах burst)
    await beat(social, first)
    response = await social.client.get(URL)
    assert response.status_code == 200
    assert response.json() == {"count": 2}
    assert "max-age" in response.headers["cache-control"]
    assert await social.infra.redis.zcard(PRESENCE_ONLINE_KEY) == 2


async def test_guest_heartbeat_is_forbidden_and_not_counted(social: Social) -> None:
    await social.publish_config()
    fresh_counter(social)
    guest = await social.client.post("/v1/me/presence", headers=social.headers(uuid7(), guest=True))
    assert guest.status_code == 403
    assert (await social.client.get(URL)).json() == {"count": 0}


async def test_players_drop_out_after_presence_ttl(social: Social) -> None:
    await social.publish_config()  # presence.ttl_s = 75
    fresh_counter(social)
    stay, leave = uuid7(), uuid7()
    await beat(social, leave)
    social.clock.advance(seconds=50)
    await beat(social, stay)
    assert (await social.client.get(URL)).json() == {"count": 2}
    social.clock.advance(seconds=30)  # leave: 80 с без heartbeat > 75, stay: 30 с
    assert (await social.client.get(URL)).json() == {"count": 1}
    social.clock.advance(seconds=60)
    assert (await social.client.get(URL)).json() == {"count": 0}


async def test_count_is_cached_between_reads(social: Social) -> None:
    await social.publish_config()
    fresh_counter(social, cache_s=5)
    await beat(social, uuid7())
    assert (await social.client.get(URL)).json() == {"count": 1}
    await beat(social, uuid7())
    assert (await social.client.get(URL)).json() == {"count": 1}  # копия ещё свежая
    social.clock.advance(seconds=6)
    assert (await social.client.get(URL)).json() == {"count": 2}


async def test_reads_are_rate_limited_per_ip(social: Social) -> None:
    await social.publish_config()
    fresh_counter(social, per_minute=2)
    for _ in range(2):
        assert (await social.client.get(URL)).status_code == 200
    limited = await social.client.get(URL)
    assert_problem(limited, 429, "social.online_count_rate_limited")
    assert "retry-after" in limited.headers
