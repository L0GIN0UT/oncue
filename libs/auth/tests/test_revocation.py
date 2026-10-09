"""Отзыв сессий по ``sid``: denylist и fail-open при недоступном хранилище."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import pytest

from gv_auth import JwtVerifier, RedisRevocationChecker, revoked_sid_key
from gv_core import UnauthenticatedError
from gv_testkit.auth import TEST_AUDIENCE, TEST_ISSUER, TestTokenIssuer

pytestmark = pytest.mark.unit


class FakeChecker:
    def __init__(self, revoked: set[str] | None = None, *, fail: bool = False) -> None:
        self.revoked = revoked or set()
        self.fail = fail
        self.calls = 0

    async def is_revoked(self, session_id: str) -> bool:
        self.calls += 1
        if self.fail:
            raise ConnectionError("redis down")
        return session_id in self.revoked


def _verifier(issuer: TestTokenIssuer, checker: Any, **kwargs: Any) -> JwtVerifier:
    jwks = issuer.jwks()

    async def load() -> Mapping[str, Any]:
        return jwks

    return JwtVerifier(
        load, issuer=TEST_ISSUER, audience=TEST_AUDIENCE, revocation=checker, **kwargs
    )


async def test_sid_claim_lands_in_principal() -> None:
    issuer = TestTokenIssuer()
    sid = str(uuid.uuid4())
    principal = await issuer.verifier().verify(issuer.issue(session_id=sid))
    assert principal.session_id == sid


async def test_revoked_session_is_rejected() -> None:
    issuer = TestTokenIssuer()
    sid = str(uuid.uuid4())
    verifier = _verifier(issuer, FakeChecker({sid}))
    with pytest.raises(UnauthenticatedError) as info:
        await verifier.verify(issuer.issue(session_id=sid))
    assert info.value.code == "auth.session_revoked"
    assert (await verifier.verify(issuer.issue(session_id=str(uuid.uuid4())))).user_id


async def test_store_outage_fails_open_and_reports() -> None:
    issuer = TestTokenIssuer()
    reported: list[str] = []
    checker = FakeChecker(fail=True)
    verifier = _verifier(issuer, checker, on_revocation_unavailable=lambda: reported.append("x"))
    assert (await verifier.verify(issuer.issue())).user_id
    assert checker.calls == 1
    assert reported == ["x"]


async def test_token_without_sid_skips_check() -> None:
    issuer = TestTokenIssuer()
    checker = FakeChecker()
    verifier = _verifier(issuer, checker)
    await verifier.verify(issuer.issue(session_id=None))
    assert checker.calls == 0


class FakeRedis:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def exists(self, *names: str) -> int:
        return sum(1 for n in names if n in self.keys)


async def test_redis_checker_reads_shared_key_format() -> None:
    redis = FakeRedis()
    checker = RedisRevocationChecker(redis)
    assert not await checker.is_revoked("abc")
    redis.keys.add(revoked_sid_key("abc"))
    assert await checker.is_revoked("abc")
    assert revoked_sid_key("abc") == "auth:revoked_sid:abc"
    assert revoked_sid_key("abc", prefix="t1:") == "t1:auth:revoked_sid:abc"
