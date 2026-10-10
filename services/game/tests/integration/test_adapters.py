"""Адаптеры game: клиент media-pipeline (по контракту), лимитер и кэш конфигов на Redis."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from game_service.domain.errors import (
    AssetsUnavailableError,
    AssetUnavailableError,
    GameRateLimitedError,
)
from game_service.infrastructure.limits import RedisRateLimiter
from game_service.infrastructure.media import (
    LINKS_PATH,
    MediaClientSettings,
    MediaPipelineAssetClient,
    UnconfiguredTokenProvider,
)
from redis.asyncio import Redis

from gv_core.ids import uuid7
from gv_game_kernel import AssetRef
from gv_testkit import FakeClock, InfraProvider, stub_service

pytestmark = pytest.mark.integration

MEDIA = "http://media.test:8000"
ASSET = AssetRef(asset_set_id=uuid7(), levels_count=6)


class StaticTokens:
    def __init__(self) -> None:
        self.calls = 0

    async def token(self) -> str:
        self.calls += 1
        return "svc-token"


def client(
    tokens: object | None = None, *, failures: int = 3, retries: int = 2
) -> MediaPipelineAssetClient:
    return MediaPipelineAssetClient(
        MediaClientSettings(
            base_url=MEDIA,
            timeout_s=1,
            retries=retries,
            breaker_failures=failures,
            breaker_cooldown_s=60,
            retry_backoff_s=0,
        ),
        tokens or StaticTokens(),  # type: ignore[arg-type]  # фейк порта TokenProvider
    )


def link_body(level: int = 1) -> dict[str, object]:
    expires = (datetime.now(UTC) + timedelta(seconds=60)).isoformat()
    return {
        "url": f"/media/a/{'a' * 32}.m4a",
        "expires_at": expires,
        "mime": "audio/mp4",
        "level": level,
    }


async def test_link_sends_service_token_and_contract_body() -> None:
    c = client()
    with stub_service(MEDIA, service="media_pipeline") as media:
        route = media.route("POST", LINKS_PATH).respond(200, json=link_body(2))
        asset = await c.link(ASSET, 2, "sess:abc")
    assert asset.level == 2 and asset.url.startswith("/media/a/")
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer svc-token"
    import json

    assert json.loads(request.content) == {
        "asset_set_id": str(ASSET.asset_set_id),
        "level": 2,
        "subject": "sess:abc",
    }
    await c.aclose()


async def test_retries_503_then_succeeds() -> None:
    c = client()
    with stub_service(MEDIA, service="media_pipeline") as media:
        route = media.route("POST", LINKS_PATH)
        route.side_effect = [httpx.Response(503), httpx.Response(200, json=link_body())]
        assert (await c.link(ASSET, 1, "sess:x")).level == 1
        assert route.call_count == 2
    await c.aclose()


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (410, AssetUnavailableError),
        (404, AssetUnavailableError),
        (429, GameRateLimitedError),
        (409, AssetsUnavailableError),
        (401, AssetsUnavailableError),
    ],
)
async def test_status_mapping_without_retry(status: int, error: type[Exception]) -> None:
    c = client()
    with stub_service(MEDIA, service="media_pipeline") as media:
        route = media.route("POST", LINKS_PATH).respond(status)
        with pytest.raises(error):
            await c.link(ASSET, 1, "sess:x")
        assert route.call_count == 1
    await c.aclose()


async def test_breaker_opens_after_failures() -> None:
    c = client(failures=2, retries=0)
    with stub_service(MEDIA, service="media_pipeline") as media:
        route = media.route("POST", LINKS_PATH).mock(side_effect=httpx.ConnectError("down"))
        for _ in range(2):
            with pytest.raises(AssetsUnavailableError):
                await c.link(ASSET, 1, "sess:x")
        calls = route.call_count
        with pytest.raises(AssetsUnavailableError):
            await c.link(ASSET, 1, "sess:x")
        assert route.call_count == calls  # открытый breaker не ходит в сеть
    await c.aclose()


async def test_unconfigured_service_auth_is_503() -> None:
    c = client(UnconfiguredTokenProvider())
    with stub_service(MEDIA, service="media_pipeline") as media:
        route = media.route("POST", LINKS_PATH).respond(200, json=link_body())
        with pytest.raises(AssetsUnavailableError):
            await c.link(ASSET, 1, "sess:x")
        assert route.call_count == 0
    await c.aclose()


@pytest.fixture
async def redis(infra: InfraProvider) -> AsyncIterator[Redis]:
    r = Redis.from_url(infra.redis_url())
    yield r
    await r.aclose()


async def test_rate_limiter_counts_per_window(redis: Redis) -> None:
    clock = FakeClock(datetime(2026, 10, 10, 9, 0, 5, tzinfo=UTC))
    limiter = RedisRateLimiter(redis, clock)
    subject = f"user-{uuid7().hex}"
    for _ in range(3):
        await limiter.hit("guess", subject, limit=3, window_s=60)
    with pytest.raises(GameRateLimitedError) as exc:
        await limiter.hit("guess", subject, limit=3, window_s=60)
    assert exc.value.details["retry_after"] == 55
    clock.advance(seconds=60)
    await limiter.hit("guess", subject, limit=3, window_s=60)


async def test_rate_limiter_fails_open() -> None:
    dead = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    limiter = RedisRateLimiter(dead, FakeClock())
    await limiter.hit("guess", "anyone", limit=0, window_s=60)
    await dead.aclose()
