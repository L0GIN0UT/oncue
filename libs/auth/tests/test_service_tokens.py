"""Сервисные токены (D13): проверка на internal-ручках и клиент identity с кэшем.

Без ``from __future__ import annotations``: FastAPI должен вычислить ``Annotated`` с
локальной зависимостью ``auth.require_service(...)`` в момент объявления ручки.
"""

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
import pytest
from fastapi import Depends, FastAPI

from gv_auth import (
    Principal,
    ServiceAuthenticator,
    ServicePrincipal,
    ServiceTokenProvider,
    SyncServiceTokenProvider,
    current_principal,
    parse_scope,
)
from gv_core import DependencyUnavailableError
from gv_service.problems import register_problem_handlers
from gv_testkit import assert_problem, service_client
from gv_testkit.auth import TestTokenIssuer

pytestmark = pytest.mark.unit

TOKEN_URL = "http://identity.test/internal/v1/service-tokens"
SECRET = "client-secret-value-123"


# ----------------------------------------------------------------------------- проверка
def _app(issuer: TestTokenIssuer, *, explicit: bool = True) -> FastAPI:
    app = FastAPI()
    app.state.jwt_verifier = issuer.verifier()
    auth = ServiceAuthenticator(issuer.verifier(audience="media_pipeline") if explicit else None)
    if not explicit:
        app.state.service_jwt_verifier = issuer.verifier(audience="media_pipeline")
    register_problem_handlers(app)

    @app.post("/internal/v1/asset-links")
    async def internal(
        p: Annotated[ServicePrincipal, Depends(auth.require_service("assets.link"))],
    ) -> dict[str, Any]:
        return {"client_id": p.client_id, "scopes": sorted(p.scopes), "jti": p.token_id}

    @app.get("/v1/me")
    async def public(p: Annotated[Principal, Depends(current_principal)]) -> dict[str, str]:
        return {"sub": str(p.user_id)}

    return app


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("explicit", [True, False])
async def test_service_token_with_scope_passes(
    token_issuer: TestTokenIssuer, service_token: Callable[..., str], explicit: bool
) -> None:
    token = service_token("game", "media_pipeline", ["assets.link", "other"])
    async with service_client(_app(token_issuer, explicit=explicit)) as client:
        response = await client.post("/internal/v1/asset-links", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["client_id"] == "game"
    assert body["scopes"] == ["assets.link", "other"]
    assert body["jti"]


async def test_internal_rejects_missing_and_foreign_tokens(
    token_issuer: TestTokenIssuer, service_token: Callable[..., str]
) -> None:
    async with service_client(_app(token_issuer)) as client:

        async def call(headers: dict[str, str] | None = None, **kw: Any) -> httpx.Response:
            return await client.post("/internal/v1/asset-links", headers=headers, **kw)

        assert_problem(await call(), 401, "auth.unauthenticated")
        assert_problem(await call({"Authorization": "Bearer "}), 401, "auth.unauthenticated")
        # пользовательский токен: aud = guessverse
        assert_problem(await call(_bearer(token_issuer.issue())), 401, "auth.token_invalid")
        # даже с правильным aud пользовательский sub не проходит
        forged_user = token_issuer.issue(audience="media_pipeline", perms=["admin.access"])
        assert_problem(await call(_bearer(forged_user)), 401, "auth.token_invalid")
        # сервисный токен другому адресату
        to_identity = service_token("game", "identity", ["assets.link"])
        assert_problem(await call(_bearer(to_identity)), 401, "auth.token_invalid")
        # без нужной области
        no_scope = service_token("admin", "media_pipeline", ["admin_sessions"])
        assert_problem(await call(_bearer(no_scope)), 403, "auth.forbidden")
        # cookie на internal не принимается
        client.cookies.set("gv_access", service_token("game", "media_pipeline", ["assets.link"]))
        assert_problem(await call(), 401, "auth.unauthenticated")


async def test_service_token_with_user_claims_is_rejected(token_issuer: TestTokenIssuer) -> None:
    """``svc:``-токен с ``sid``/``roles``/``perms`` — подделка или ошибка, не принимаем."""
    async with service_client(_app(token_issuer)) as client:
        for extra in ({"sid": "x"}, {"roles": ["admin"]}, {"perms": []}, {"gst": True}):
            token = token_issuer.issue(
                user_id=None, audience="media_pipeline", session_id=None, extra=extra
            )
            forged = token_issuer.issue(
                audience="media_pipeline",
                session_id=None,
                extra={"sub": "svc:game", "scope": "assets.link", **extra},
            )
            for t in (token, forged):
                response = await client.post("/internal/v1/asset-links", headers=_bearer(t))
                assert_problem(response, 401, "auth.token_invalid")


@pytest.mark.parametrize("client_id", ["", "Game", "game/../x", "a" * 65])
async def test_malformed_client_id_is_rejected(
    token_issuer: TestTokenIssuer, client_id: str
) -> None:
    token = token_issuer.issue_service(client_id, "media_pipeline", ["assets.link"])
    async with service_client(_app(token_issuer)) as client:
        response = await client.post("/internal/v1/asset-links", headers=_bearer(token))
        assert_problem(response, 401, "auth.token_invalid")


async def test_expired_service_token(token_issuer: TestTokenIssuer) -> None:
    old = datetime.now(UTC) - timedelta(hours=1)
    token = token_issuer.issue_service("game", "media_pipeline", ["assets.link"], now=old)
    async with service_client(_app(token_issuer)) as client:
        response = await client.post("/internal/v1/asset-links", headers=_bearer(token))
        assert_problem(response, 401, "auth.token_expired")


async def test_service_token_does_not_pass_public(
    token_issuer: TestTokenIssuer, service_token: Callable[..., str]
) -> None:
    async with service_client(_app(token_issuer)) as client:
        for audience in ("media_pipeline", "guessverse"):
            token = service_token("game", audience, ["assets.link"])
            response = await client.get("/v1/me", headers=_bearer(token))
            assert_problem(response, 401, "auth.token_invalid")


async def test_missing_service_verifier_fails_closed(service_token: Callable[..., str]) -> None:
    app = FastAPI()
    register_problem_handlers(app)
    auth = ServiceAuthenticator()

    @app.post("/internal")
    async def internal(_: Annotated[ServicePrincipal, Depends(auth.require_service("x"))]) -> None:
        return None

    async with service_client(app) as client:
        token = service_token("game", "media_pipeline", ["x"])
        assert (await client.post("/internal", headers=_bearer(token))).status_code == 500


def test_parse_scope_drops_garbage() -> None:
    assert parse_scope("a.b  c:d BAD x") == frozenset({"a.b", "c:d", "x"})
    assert parse_scope(["a"]) == frozenset()
    assert parse_scope(None) == frozenset()


# ----------------------------------------------------------------------------- клиент
class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Identity:
    """Заглушка ``POST /internal/v1/service-tokens``."""

    def __init__(self, ttl: int = 600) -> None:
        self.ttl = ttl
        self.calls: list[dict[str, Any]] = []
        self.fail: int | Exception | None = None
        self.body: Any = None
        self.gate: asyncio.Event | None = None

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(json.loads(request.content))
        if isinstance(self.fail, Exception):
            raise self.fail
        if isinstance(self.fail, int):
            return httpx.Response(self.fail, json={"code": "x"})
        if self.body is not None:
            return httpx.Response(200, json=self.body)
        n = len(self.calls)
        return httpx.Response(
            200, json={"access_token": f"tok-{n}", "token_type": "Bearer", "expires_in": self.ttl}
        )

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if self.gate is not None:
            await self.gate.wait()
        return self._respond(request)

    def handle_sync(self, request: httpx.Request) -> httpx.Response:
        return self._respond(request)


def _provider(
    identity: Identity, clock: FakeClock, failures: list[int] | None = None
) -> ServiceTokenProvider:
    return ServiceTokenProvider(
        TOKEN_URL,
        "game",
        SECRET,
        "media_pipeline",
        transport=httpx.MockTransport(identity.handle),
        clock=clock,
        retry_backoff_s=5,
        expiry_margin_s=5,
        on_refresh_failure=(lambda: failures.append(1)) if failures is not None else None,
    )


async def test_token_is_cached_and_refreshed_at_half_ttl() -> None:
    identity, clock = Identity(ttl=600), FakeClock()
    provider = _provider(identity, clock)
    try:
        assert await provider.token() == "tok-1"
        assert identity.calls == [
            {"client_id": "game", "client_secret": SECRET, "audience": "media_pipeline"}
        ]
        clock.now += 299
        assert await provider.token() == "tok-1"
        assert len(identity.calls) == 1
        clock.now += 1  # 50 % TTL
        assert await provider.token() == "tok-2"
        assert await provider.headers() == {"Authorization": "Bearer tok-2"}
        assert len(identity.calls) == 2
    finally:
        await provider.aclose()


async def test_concurrent_callers_share_one_refresh() -> None:
    identity, clock = Identity(), FakeClock()
    identity.gate = asyncio.Event()
    provider = _provider(identity, clock)
    try:
        tasks = [asyncio.create_task(provider.token()) for _ in range(20)]
        await asyncio.sleep(0)
        identity.gate.set()
        assert set(await asyncio.gather(*tasks)) == {"tok-1"}
        assert len(identity.calls) == 1
    finally:
        await provider.aclose()


async def test_identity_down_keeps_old_token_until_expiry() -> None:
    identity, clock, failures = Identity(ttl=600), FakeClock(), []
    provider = _provider(identity, clock, failures)
    try:
        assert await provider.token() == "tok-1"
        identity.fail = 503
        clock.now += 300
        assert await provider.token() == "tok-1"  # старый токен ещё жив
        assert failures == [1]
        clock.now += 1
        assert await provider.token() == "tok-1"  # пауза: в identity не ходим
        assert len(identity.calls) == 2
        clock.now += 5
        assert await provider.token() == "tok-1"  # пауза кончилась — новая попытка
        assert len(identity.calls) == 3
        assert failures == [1, 1]
        clock.now = 1000 + 600 - 5  # запас до истечения исчерпан
        with pytest.raises(DependencyUnavailableError) as exc:
            await provider.token()
        assert exc.value.code == "auth.service_token_unavailable"
        identity.fail = None
        clock.now += 10
        assert await provider.token() == "tok-5"  # identity вернулся
    finally:
        await provider.aclose()


async def test_identity_down_without_cache_fails_fast_during_backoff() -> None:
    identity, clock = Identity(), FakeClock()
    identity.fail = httpx.ConnectError("refused")
    provider = _provider(identity, clock)
    try:
        with pytest.raises(DependencyUnavailableError) as exc:
            await provider.token()
        assert exc.value.code == "auth.service_token_unavailable"
        with pytest.raises(DependencyUnavailableError):
            await provider.token()
        assert len(identity.calls) == 1  # вторая попытка — без запроса
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "body",
    [
        {"access_token": "", "token_type": "Bearer", "expires_in": 600},
        {"access_token": "t", "token_type": "mac", "expires_in": 600},
        {"access_token": "t", "token_type": "Bearer", "expires_in": 0},
        {"access_token": "t", "token_type": "Bearer", "expires_in": True},
        {"access_token": "t", "token_type": "Bearer"},
        ["not", "an", "object"],
    ],
)
async def test_malformed_identity_response_is_unavailable(body: Any) -> None:
    identity, clock = Identity(), FakeClock()
    identity.body = body
    provider = _provider(identity, clock)
    try:
        with pytest.raises(DependencyUnavailableError):
            await provider.token()
    finally:
        await provider.aclose()


async def test_rejected_credentials_are_unavailable_and_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    identity, clock = Identity(), FakeClock()
    identity.fail = 401
    provider = _provider(identity, clock)
    caplog.set_level(logging.DEBUG)
    try:
        with pytest.raises(DependencyUnavailableError) as exc:
            await provider.token()
        assert exc.value.details == {"status": 401}
    finally:
        await provider.aclose()
    assert "auth.service_token_refresh_failed" in caplog.text
    assert SECRET not in caplog.text
    assert SECRET not in repr(provider)


async def test_invalidate_forces_new_token() -> None:
    identity, clock = Identity(), FakeClock()
    provider = _provider(identity, clock)
    try:
        assert await provider.token() == "tok-1"
        provider.invalidate()
        assert await provider.token() == "tok-2"
    finally:
        await provider.aclose()


def test_provider_validates_options() -> None:
    with pytest.raises(ValueError, match="refresh_ratio"):
        ServiceTokenProvider(TOKEN_URL, "game", SECRET, "m", refresh_ratio=0)
    with pytest.raises(ValueError, match="retry_backoff_s"):
        ServiceTokenProvider(TOKEN_URL, "game", SECRET, "m", retry_backoff_s=-1)


def test_sync_provider_caches_and_is_thread_safe() -> None:
    identity, clock = Identity(ttl=600), FakeClock()
    provider = SyncServiceTokenProvider(
        TOKEN_URL,
        "admin",
        SECRET,
        "identity",
        transport=httpx.MockTransport(identity.handle_sync),
        clock=clock,
    )
    results: list[str] = []
    try:
        threads = [
            threading.Thread(target=lambda: results.append(provider.token())) for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert set(results) == {"tok-1"}
        assert len(identity.calls) == 1
        assert identity.calls[0]["audience"] == "identity"
        clock.now += 300
        assert provider.headers() == {"Authorization": "Bearer tok-2"}
        identity.fail = 500
        clock.now += 300
        assert provider.token() == "tok-2"  # старый токен до истечения
        provider.invalidate()
        clock.now += 10
        with pytest.raises(DependencyUnavailableError):
            provider.token()
    finally:
        provider.close()
    assert SECRET not in repr(provider)
