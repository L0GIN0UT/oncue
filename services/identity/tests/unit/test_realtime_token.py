"""Connection-токен Centrifugo (I1 плана этапа 4, D9, ADR 0035): claims, лимит, подпись."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from uuid import UUID

import jwt
import pytest
from identity_service.application.commands.realtime import (
    IssueRealtimeToken,
    RealtimeTokenConfig,
    realtime_claims,
)
from identity_service.infrastructure.realtime import HmacRealtimeTokenSigner
from identity_service.infrastructure.security import KeyConfigError
from identity_service.settings import Settings
from identity_service.wiring import realtime_config, realtime_signer

from gv_core import DependencyUnavailableError, RateLimitedError
from gv_testkit import FakeClock

pytestmark = pytest.mark.unit

World = Any  # фейковый мир из conftest
USER = UUID("0192f0a0-0000-7000-8000-000000000001")
SECRET = "s" * 40
CONFIG = RealtimeTokenConfig(ttl_s=600, audience="centrifugo", per_user=3, window_s=60)


class CapturingSigner:
    def __init__(self) -> None:
        self.claims: list[Mapping[str, Any]] = []

    def sign(self, claims: Mapping[str, Any]) -> str:
        self.claims.append(dict(claims))
        return f"token-{len(self.claims)}"


def test_claims_are_minimal() -> None:
    clock = FakeClock()
    claims = realtime_claims(USER, now=clock.now(), ttl_s=600, audience="centrifugo")
    # Ровно sub/aud/iat/exp: без channels/subs/info/perms — права даёт subscription-токен match.
    assert set(claims) == {"sub", "aud", "iat", "exp"}
    assert claims["sub"] == str(USER)
    assert claims["aud"] == "centrifugo"
    assert claims["iat"] == int(clock.now().timestamp())
    assert claims["exp"] - claims["iat"] == 600


async def test_issue_uses_clock_and_returns_expiry(world: World) -> None:
    signer = CapturingSigner()
    issue = IssueRealtimeToken(signer, world.limiter, world.clock, CONFIG)
    issued = await issue.execute(USER)
    assert issued.token == "token-1"
    assert issued.expires_at == world.clock.now() + timedelta(seconds=600)
    assert signer.claims[0]["exp"] == int(issued.expires_at.timestamp())


async def test_rate_limit_per_user(world: World) -> None:
    signer = CapturingSigner()
    issue = IssueRealtimeToken(signer, world.limiter, world.clock, CONFIG)
    for _ in range(3):
        await issue.execute(USER)
    with pytest.raises(RateLimitedError):
        await issue.execute(USER)
    # Счётчик — на пользователя: другой пользователь не задет.
    await issue.execute(UUID("0192f0a0-0000-7000-8000-000000000002"))
    assert len(signer.claims) == 4
    # В ключе лимитера нет user_id открытым текстом.
    assert all(str(USER) not in key for key in world.limiter.counts)


async def test_not_configured_is_503(world: World) -> None:
    issue = IssueRealtimeToken(None, world.limiter, world.clock, CONFIG)
    with pytest.raises(DependencyUnavailableError) as exc:
        await issue.execute(USER)
    assert exc.value.code == "realtime.not_configured"
    # Отказ до лимитера: счётчик не тратится впустую.
    assert world.limiter.counts == {}


def test_hmac_signer_hs256() -> None:
    clock = FakeClock()
    claims = realtime_claims(USER, now=clock.now(), ttl_s=600, audience="centrifugo")
    token = HmacRealtimeTokenSigner(SECRET).sign(claims)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "HS256"
    assert "kid" not in header
    decoded = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        audience="centrifugo",
        options={"verify_exp": False, "verify_iat": False},
    )
    assert decoded == claims


def test_hmac_signer_rejects_short_secret() -> None:
    with pytest.raises(ValueError, match="короче"):
        HmacRealtimeTokenSigner("short")


def _rt(**extra: Any) -> dict[str, Any]:
    return {"api_url": "http://centrifugo:8000/api", "api_key": "k" * 40, **extra}


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_missing_secret_fails_startup_outside_local_ci(environment: str) -> None:
    for realtime in (None, _rt()):
        settings = Settings(environment=environment, realtime=realtime)
        with pytest.raises(KeyConfigError, match="CONNECTION_TOKEN_SECRET"):
            realtime_signer(settings, serving=True)
        # Сборка без инфраструктуры (экспорт OpenAPI) — без отказа.
        assert realtime_signer(settings, serving=False) is None


@pytest.mark.parametrize("environment", ["local", "ci"])
def test_missing_secret_in_local_ci_means_503(environment: str) -> None:
    assert realtime_signer(Settings(environment=environment), serving=True) is None


def test_configured_secret_builds_signer() -> None:
    settings = Settings(
        environment="production",
        realtime=_rt(connection_token_secret=SECRET, token_ttl_s=300),
        realtime_token_limit={"per_user": 7, "window_s": 30},
    )
    assert isinstance(realtime_signer(settings, serving=True), HmacRealtimeTokenSigner)
    assert realtime_config(settings) == RealtimeTokenConfig(
        ttl_s=300, audience="centrifugo", per_user=7, window_s=30
    )


@pytest.mark.parametrize(
    "realtime",
    [
        _rt(connection_token_secret=SECRET, subscription_token_secret="u" * 40),
        _rt(connection_token_secret=SECRET, token_audience="guessverse"),
        _rt(connection_token_secret=SECRET, token_audience="identity"),
    ],
)
def test_foreign_secret_or_colliding_audience_is_config_error(realtime: dict[str, Any]) -> None:
    with pytest.raises(KeyConfigError):
        realtime_signer(Settings(environment="local", realtime=realtime), serving=True)
