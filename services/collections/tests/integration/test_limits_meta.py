"""Лимитер на реальном Redis (окна, fail-open/closed) и загрузка словарей ``meta``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from collections_service.domain.errors import LimiterUnavailableError
from collections_service.infrastructure.limits import RedisRateLimiter, UnconfiguredRateLimiter
from collections_service.infrastructure.meta import load_meta, parse_meta
from redis.asyncio import Redis

from gv_core.ids import uuid7
from gv_testkit import FakeClock, InfraProvider

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis(infra: InfraProvider) -> AsyncIterator[Redis]:
    client = Redis.from_url(infra.redis_url())
    yield client
    await client.aclose()


async def test_fixed_window_limits_per_subject(redis: Redis) -> None:
    clock = FakeClock(datetime(2026, 10, 16, 12, 0, 30, tzinfo=UTC))
    limiter = RedisRateLimiter(redis, clock)
    subject, other = f"user:{uuid7().hex}", f"user:{uuid7().hex}"
    assert [await limiter.hit("t", subject, limit=2, window_s=60) for _ in range(2)] == [None, None]
    assert await limiter.hit("t", subject, limit=2, window_s=60) == 30
    assert await limiter.hit("t", other, limit=2, window_s=60) is None
    clock.advance(timedelta(seconds=31))
    assert await limiter.hit("t", subject, limit=2, window_s=60) is None
    keys = [k.decode() async for k in redis.scan_iter("collections:rl:t:*")]
    assert keys and all(subject.split(":")[1] not in k for k in keys)  # id не хранится открыто


async def test_unavailable_redis_fails_open_or_closed() -> None:
    clock = FakeClock()
    broken = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    try:
        limiter = RedisRateLimiter(broken, clock)
        assert await limiter.hit("likes", "u", limit=1, window_s=60) is None
        with pytest.raises(LimiterUnavailableError):
            await limiter.hit("publish", "u", limit=1, window_s=60, fail_closed=True)
    finally:
        await broken.aclose()
    unconfigured = UnconfiguredRateLimiter()
    assert await unconfigured.hit("likes", "u", limit=1, window_s=60) is None
    with pytest.raises(LimiterUnavailableError):
        await unconfigured.hit("publish", "u", limit=1, window_s=60, fail_closed=True)


def test_builtin_meta_is_valid() -> None:
    meta = load_meta(None)
    assert meta.has_cover("night") and meta.tag("horror") is not None
    assert all(t.domains for t in meta.tags)


def test_custom_meta_file(tmp_path: Path) -> None:
    path = tmp_path / "meta.yaml"
    path.write_text(
        "cover_presets:\n  - {id: one, gradient: ['#000000', '#ffffff'], emoji: '🎬'}\n"
        "tags:\n  - {key: noir, title_key: t.noir, domains: [film], search: [нуар]}\n",
        encoding="utf-8",
    )
    meta = load_meta(path)
    assert [p.id for p in meta.cover_presets] == ["one"]
    assert meta.search_terms(["noir"]) == ["noir", "нуар"]


@pytest.mark.parametrize(
    "text",
    [
        "cover_presets: []\ntags: []\n",
        "cover_presets:\n  - {id: a, gradient: ['#000000', '#ffffff'], emoji: 'кот'}\ntags: []\n",
        "cover_presets:\n  - {id: a, gradient: ['red', '#ffffff'], emoji: '🎬'}\ntags: []\n",
        "cover_presets:\n  - {id: a, gradient: ['#000000', '#ffffff'], emoji: '🎬'}\n"
        "  - {id: a, gradient: ['#000000', '#ffffff'], emoji: '🎬'}\ntags: []\n",
        "cover_presets:\n  - {id: a, gradient: ['#000000', '#ffffff'], emoji: '🎬'}\n"
        "tags:\n  - {key: x, title_key: t.x, domains: [anime]}\n",
        ": : :",
    ],
)
def test_broken_meta_fails_fast(text: str) -> None:
    with pytest.raises(ValueError, match=r"конфиг словарей|повторяются|эмодзи пресета"):
        parse_meta(text)
