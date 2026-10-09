from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from gv_auth import DEFAULT_LEEWAY_S, JwtVerifier, Sanction
from gv_core import DependencyUnavailableError, UnauthenticatedError
from gv_testkit.auth import TEST_AUDIENCE, TEST_ISSUER, TestTokenIssuer

pytestmark = pytest.mark.unit


class CountingLoader:
    def __init__(self, jwks: Mapping[str, Any] | None, *, fail: bool = False) -> None:
        self.jwks = jwks
        self.fail = fail
        self.calls = 0

    async def __call__(self) -> Mapping[str, Any]:
        self.calls += 1
        if self.fail or self.jwks is None:
            raise ConnectionError("identity недоступен")
        return self.jwks


class FakeTime:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _verifier(loader: CountingLoader, clock: FakeTime | None = None) -> JwtVerifier:
    return JwtVerifier(
        loader,
        issuer=TEST_ISSUER,
        audience=TEST_AUDIENCE,
        clock=clock or FakeTime(),
        min_refresh_interval_s=30,
    )


async def test_valid_token_produces_principal() -> None:
    issuer = TestTokenIssuer()
    user = uuid.uuid4()
    token = issuer.issue(
        user_id=user, roles=["moderator"], perms=["comments.delete_any"], sanctions=8 | 16
    )
    principal = await _verifier(CountingLoader(issuer.jwks())).verify(token)
    assert principal.user_id == user
    assert principal.roles == frozenset({"moderator"})
    assert principal.has_perm("comments.delete_any")
    assert principal.is_sanctioned(Sanction.CHAT_MUTE)
    assert not principal.is_sanctioned(Sanction.RANKED)
    assert principal.plan == "free_all"


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"ttl": timedelta(minutes=-5)}, "auth.token_expired"),
        ({"audience": "other-app"}, "auth.token_invalid"),
        ({"issuer": "https://evil"}, "auth.token_invalid"),
    ],
)
async def test_rejected_claims(kwargs: dict[str, Any], code: str) -> None:
    issuer = TestTokenIssuer()
    with pytest.raises(UnauthenticatedError) as info:
        await _verifier(CountingLoader(issuer.jwks())).verify(issuer.issue(**kwargs))
    assert info.value.code == code


async def test_hs256_and_none_algorithms_are_rejected() -> None:
    issuer = TestTokenIssuer()
    verifier = _verifier(CountingLoader(issuer.jwks()))
    claims = {
        "sub": str(uuid.uuid4()),
        "iss": TEST_ISSUER,
        "aud": TEST_AUDIENCE,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
    }
    hs = jwt.encode(claims, "x" * 32, algorithm="HS256", headers={"kid": issuer.kid})
    # PyJWT принимает key=None для alg=none, но стабы типов этого не знают.
    none = jwt.encode(claims, None, algorithm="none", headers={"kid": issuer.kid})  # type: ignore[arg-type]
    for token in (hs, none):
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(token)


async def test_garbage_and_missing_kid() -> None:
    issuer = TestTokenIssuer()
    verifier = _verifier(CountingLoader(issuer.jwks()))
    with pytest.raises(UnauthenticatedError, match="некорректный"):
        await verifier.verify("not.a.jwt")
    no_kid = jwt.encode({"sub": "x"}, issuer._private, algorithm="EdDSA")
    with pytest.raises(UnauthenticatedError, match="kid"):
        await verifier.verify(no_kid)


async def test_unknown_kid_refresh_is_rate_limited() -> None:
    issuer, stranger = TestTokenIssuer("k1"), TestTokenIssuer("k-unknown")
    loader, clock = CountingLoader(issuer.jwks()), FakeTime()
    verifier = _verifier(loader, clock)
    await verifier.verify(issuer.issue())
    assert loader.calls == 1
    clock.t += 1
    for _ in range(20):
        with pytest.raises(UnauthenticatedError):
            await verifier.verify(stranger.issue())
    assert loader.calls == 1  # подставные kid не заставляют долбить identity
    clock.t += 31
    with pytest.raises(UnauthenticatedError):
        await verifier.verify(stranger.issue())
    assert loader.calls == 2


async def test_key_rotation_is_picked_up() -> None:
    old, new = TestTokenIssuer("k1"), TestTokenIssuer("k2")
    loader, clock = CountingLoader(old.jwks()), FakeTime()
    verifier = _verifier(loader, clock)
    await verifier.verify(old.issue())
    loader.jwks = {"keys": old.jwks()["keys"] + new.jwks()["keys"]}
    clock.t += 60
    assert (await verifier.verify(new.issue())).user_id


async def test_identity_down_uses_cached_keys_or_fails_cleanly() -> None:
    issuer = TestTokenIssuer()
    loader, clock = CountingLoader(issuer.jwks()), FakeTime()
    verifier = _verifier(loader, clock)
    await verifier.verify(issuer.issue())
    loader.fail = True
    clock.t += 7200  # кэш просрочен, но identity лежит — работаем на старых ключах
    assert (await verifier.verify(issuer.issue())).user_id

    with pytest.raises(DependencyUnavailableError):
        await _verifier(CountingLoader(None)).verify(issuer.issue())


async def test_identity_outage_does_not_hammer_jwks_after_ttl() -> None:
    """Регрессия: после истечения TTL при лежащем identity каждый запрос ходил за JWKS
    (и ждал таймаут), потому что троттлинг считался от последней УСПЕШНОЙ загрузки."""
    issuer = TestTokenIssuer()
    loader, clock = CountingLoader(issuer.jwks()), FakeTime()
    verifier = _verifier(loader, clock)
    await verifier.verify(issuer.issue())
    loader.fail = True
    clock.t += 7200
    for _ in range(50):
        assert (await verifier.verify(issuer.issue())).user_id
    assert loader.calls == 2  # одна попытка, дальше — кэш до следующего окна
    clock.t += 31
    await verifier.verify(issuer.issue())
    assert loader.calls == 3


async def test_empty_jwks_keeps_previous_keys() -> None:
    issuer = TestTokenIssuer()
    loader, clock = CountingLoader(issuer.jwks()), FakeTime()
    verifier = _verifier(loader, clock)
    await verifier.verify(issuer.issue())
    loader.jwks = {"keys": []}
    clock.t += 7200
    assert (await verifier.verify(issuer.issue())).user_id
    with pytest.raises(DependencyUnavailableError):
        await _verifier(CountingLoader({"keys": []})).verify(issuer.issue())


async def test_malformed_sanctions_claim_is_unauthenticated_not_500() -> None:
    issuer = TestTokenIssuer()
    verifier = _verifier(CountingLoader(issuer.jwks()))
    with pytest.raises(UnauthenticatedError):
        await verifier.verify(issuer.issue(extra={"sanctions": "много"}))


async def test_leeway_is_exposed_for_denylist_ttl() -> None:
    """identity держит denylist sid access TTL + leeway: значение должно быть доступно."""
    issuer = TestTokenIssuer()
    default = JwtVerifier(CountingLoader(issuer.jwks()), issuer=TEST_ISSUER, audience=TEST_AUDIENCE)
    assert default.leeway_s == DEFAULT_LEEWAY_S
    custom = JwtVerifier(
        CountingLoader(issuer.jwks()), issuer=TEST_ISSUER, audience=TEST_AUDIENCE, leeway_s=5
    )
    assert custom.leeway_s == 5
