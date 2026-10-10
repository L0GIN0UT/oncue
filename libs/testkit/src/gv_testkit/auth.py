"""Тестовые JWT: свой ключ Ed25519, выпуск токенов с нужными правами, верификатор."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gv_auth.service import SERVICE_SUBJECT_PREFIX
from gv_auth.verifier import JwtVerifier
from gv_core.ids import uuid7

TEST_ISSUER = "https://identity.test"
TEST_AUDIENCE = "guessverse"
SERVICE_TOKEN_TTL = timedelta(seconds=600)


class TestTokenIssuer:
    """Выпускает токены, которые принимает ``verifier()``.

    Пример::

        issuer = TestTokenIssuer()
        token = issuer.issue(perms=["comments.delete_any"])
        client.headers["Authorization"] = f"Bearer {token}"
    """

    __test__ = False  # pytest: это не тестовый класс

    def __init__(self, kid: str = "test-key-1") -> None:
        self.kid = kid
        self._private = Ed25519PrivateKey.generate()

    def jwks(self) -> dict[str, Any]:
        """Публичный JWKS с одним ключом."""
        jwk = json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(self._private.public_key()))
        jwk.update({"kid": self.kid, "use": "sig", "alg": "EdDSA"})
        return {"keys": [jwk]}

    def issue(
        self,
        *,
        user_id: UUID | None = None,
        roles: Iterable[str] = ("user",),
        perms: Iterable[str] = (),
        sanctions: int = 0,
        plan: str = "free_all",
        ttl: timedelta = timedelta(minutes=15),
        now: datetime | None = None,
        extra: Mapping[str, Any] | None = None,
        audience: str = TEST_AUDIENCE,
        issuer: str = TEST_ISSUER,
        session_id: str | None = "",
    ) -> str:
        """Выпустить подписанный access-токен.

        Args:
            session_id: Claim ``sid``. По умолчанию — новый случайный (как у настоящих
                токенов identity); ``None`` — без ``sid``.
        """
        issued = now or datetime.now(UTC)
        claims: dict[str, Any] = {
            "sub": str(user_id or uuid7()),
            "iss": issuer,
            "aud": audience,
            "iat": issued,
            "exp": issued + ttl,
            "jti": str(uuid7()),
            "roles": list(roles),
            "perms": list(perms),
            "sanctions": sanctions,
            "plan": plan,
        }
        if session_id is not None:
            claims["sid"] = session_id or str(uuid7())
        claims.update(extra or {})
        return jwt.encode(claims, self._private, algorithm="EdDSA", headers={"kid": self.kid})

    def issue_guest(
        self,
        *,
        guest_id: UUID | None = None,
        ttl: timedelta = timedelta(minutes=15),
        now: datetime | None = None,
        session_id: str | None = "",
    ) -> str:
        """Гостевой access-токен, как его выдаёт identity (D5 плана этапа 2).

        ``sub`` — uuid7 гостя, ``roles = ["guest"]``, ``perms = []``, ``gst = true``.
        """
        return self.issue(
            user_id=guest_id or uuid7(),
            roles=("guest",),
            perms=(),
            ttl=ttl,
            now=now,
            session_id=session_id,
            extra={"gst": True},
        )

    def issue_service(
        self,
        client_id: str,
        audience: str,
        scopes: Iterable[str] = (),
        *,
        ttl: timedelta = SERVICE_TOKEN_TTL,
        now: datetime | None = None,
        issuer: str = TEST_ISSUER,
    ) -> str:
        """Сервисный токен (client credentials, D13): ``sub = svc:<client_id>``, ``aud`` —
        вызываемый сервис, ``scope`` — области через пробел; без ``sid``, ``roles``, ``perms``.
        """
        issued = now or datetime.now(UTC)
        claims: dict[str, Any] = {
            "sub": f"{SERVICE_SUBJECT_PREFIX}{client_id}",
            "iss": issuer,
            "aud": audience,
            "iat": issued,
            "exp": issued + ttl,
            "jti": str(uuid7()),
            "scope": " ".join(scopes),
        }
        return jwt.encode(claims, self._private, algorithm="EdDSA", headers={"kid": self.kid})

    def verifier(self, audience: str = TEST_AUDIENCE) -> JwtVerifier:
        """Верификатор, доверяющий только этому издателю.

        Args:
            audience: Ожидаемый ``aud``: ``guessverse`` для пользовательских токенов, имя
                сервиса — для сервисных.
        """
        jwks = self.jwks()

        async def load() -> Mapping[str, Any]:
            return jwks

        return JwtVerifier(load, issuer=TEST_ISSUER, audience=audience)
