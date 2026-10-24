"""Криптография identity: argon2id, ключи подписи, выпуск JWT, случайность."""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from argon2 import PasswordHasher as _Argon2
from argon2 import Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from gv_auth import SERVICE_SUBJECT_PREFIX
from gv_core.clock import Clock
from gv_core.ids import uuid7
from identity_service.application.ports import IssuedAccess
from identity_service.application.ports.service_tokens import IssuedServiceToken
from identity_service.domain.guest import GUEST_ROLE

# ----------------------------------------------------------------------------- пароли


class Argon2Hasher:
    """argon2id (RFC 9106). Вычисление — в пуле потоков: не блокирует event loop.

    Одновременных вычислений не больше ``max_concurrency``: каждое берёт ``memory_kib``
    памяти, и поток входов (или перебор) иначе исчерпал бы память и пул потоков процесса.
    Лишние запросы ждут в очереди семафора.
    """

    def __init__(
        self, *, time_cost: int, memory_kib: int, parallelism: int = 1, max_concurrency: int = 4
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency должен быть >= 1")
        self._ph = _Argon2(
            time_cost=time_cost, memory_cost=memory_kib, parallelism=parallelism, type=Type.ID
        )
        self._dummy: str | None = None
        self._slots = asyncio.Semaphore(max_concurrency)

    async def hash(self, password: str) -> str:
        """Новый хэш с солью."""
        async with self._slots:
            return await asyncio.to_thread(self._ph.hash, password)

    async def verify(self, password_hash: str, password: str) -> bool:
        """Проверка; битый хэш — ``False`` (не 500)."""
        async with self._slots:
            return await asyncio.to_thread(self._verify_sync, password_hash, password)

    def _verify_sync(self, password_hash: str, password: str) -> bool:
        try:
            return self._ph.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        """Параметры хэша отличаются от текущих настроек."""
        try:
            return self._ph.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True

    async def verify_dummy(self, password: str) -> None:
        """Проверка против фиктивного хэша с теми же параметрами (выравнивание времени)."""
        if self._dummy is None:
            self._dummy = await self.hash(secrets.token_urlsafe(16))
        await self.verify(self._dummy, password)


class SystemSecrets:
    """Криптостойкая случайность (``secrets``)."""

    def token(self, nbytes: int) -> str:
        """Случайная строка base64url."""
        return secrets.token_urlsafe(nbytes)


# ----------------------------------------------------------------------------- ключи JWT


@dataclass(frozen=True, slots=True)
class SigningKey:
    """Ключ подписи. ``not_after`` — до какого момента публиковать ключ в JWKS."""

    kid: str
    private_key: ed25519.Ed25519PrivateKey | ec.EllipticCurvePrivateKey
    active: bool
    not_after: datetime | None = None

    @property
    def algorithm(self) -> str:
        """EdDSA для Ed25519, ES256 для P-256."""
        return "EdDSA" if isinstance(self.private_key, ed25519.Ed25519PrivateKey) else "ES256"

    def public_jwk(self) -> dict[str, Any]:
        """Публичная часть в формате JWK."""
        public = self.private_key.public_key()
        if isinstance(public, ed25519.Ed25519PublicKey):
            raw = jwt.algorithms.OKPAlgorithm.to_jwk(public)
        else:
            raw = jwt.algorithms.ECAlgorithm.to_jwk(public)
        jwk: dict[str, Any] = json.loads(raw)
        jwk.update({"kid": self.kid, "use": "sig", "alg": self.algorithm})
        return jwk


class KeyConfigError(ValueError):
    """Некорректная конфигурация ключей подписи — процесс не должен стартовать."""


def parse_signing_keys(raw: str) -> list[SigningKey]:
    """Разобрать ``GV_JWT__SIGNING_KEYS``: JSON ``[{kid, private_pem, active, not_after?}]``.

    Требования: ровно один активный ключ, уникальные ``kid``, только Ed25519 или EC P-256.
    Текст ошибки не содержит материал ключа.
    """
    try:
        items = json.loads(raw)
    except ValueError as exc:
        raise KeyConfigError("GV_JWT__SIGNING_KEYS: некорректный JSON") from exc
    if not isinstance(items, list) or not items:
        raise KeyConfigError("GV_JWT__SIGNING_KEYS: нужен непустой список ключей")
    keys: list[SigningKey] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("kid") or not item.get("private_pem"):
            raise KeyConfigError("ключ подписи: нужны kid и private_pem")
        kid = str(item["kid"])
        try:
            private = load_pem_private_key(str(item["private_pem"]).encode(), password=None)
        except (ValueError, TypeError) as exc:
            raise KeyConfigError(f"ключ {kid}: не удалось прочитать PEM") from exc
        if isinstance(private, ec.EllipticCurvePrivateKey):
            if not isinstance(private.curve, ec.SECP256R1):
                raise KeyConfigError(f"ключ {kid}: поддерживается только кривая P-256")
        elif not isinstance(private, ed25519.Ed25519PrivateKey):
            raise KeyConfigError(f"ключ {kid}: поддерживаются только Ed25519 и P-256")
        not_after = item.get("not_after")
        keys.append(
            SigningKey(
                kid=kid,
                private_key=private,
                active=bool(item.get("active", False)),
                not_after=datetime.fromisoformat(not_after) if not_after else None,
            )
        )
    if len({k.kid for k in keys}) != len(keys):
        raise KeyConfigError("GV_JWT__SIGNING_KEYS: повторяющиеся kid")
    if sum(k.active for k in keys) != 1:
        raise KeyConfigError("GV_JWT__SIGNING_KEYS: должен быть ровно один активный ключ")
    return keys


def generate_dev_key(kid: str = "dev-ephemeral") -> SigningKey:
    """Временный ключ для local/ci без настроенных ключей (токены не переживут рестарт)."""
    return SigningKey(kid=kid, private_key=ed25519.Ed25519PrivateKey.generate(), active=True)


class StaticKeyProvider:
    """Ключи из конфигурации: активный подписывает, все неистёкшие — в JWKS."""

    def __init__(self, keys: list[SigningKey], clock: Clock) -> None:
        active = [k for k in keys if k.active]
        if len(active) != 1:
            raise KeyConfigError("должен быть ровно один активный ключ")
        self._keys = keys
        self.active = active[0]
        self._clock = clock

    def jwks(self) -> dict[str, Any]:
        """JWKS: активный ключ и прежние, пока не истёк ``not_after``."""
        now = self._clock.now()
        return {
            "keys": [
                k.public_jwk()
                for k in self._keys
                if k.active or k.not_after is None or k.not_after > now
            ]
        }


class JwtIssuer:
    """Выпуск access-токенов (claims — docs/plans/stage-1.md §4, libs/auth)."""

    def __init__(
        self,
        keys: StaticKeyProvider,
        clock: Clock,
        *,
        issuer: str,
        audience: str,
        access_ttl_s: int,
    ) -> None:
        self._keys, self._clock = keys, clock
        self._issuer, self._audience, self._ttl = issuer, audience, access_ttl_s

    def issue_access(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        roles: Iterable[str],
        perms: Iterable[str],
        sanctions: int = 0,
    ) -> IssuedAccess:
        """Подписать токен активным ключом; ``sanctions`` - маска действующих санкций."""
        return self._sign(
            {
                "sub": str(user_id),
                "sid": str(session_id),
                "roles": list(roles),
                "perms": list(perms),
                "sanctions": sanctions,
                "plan": "free_all",
            }
        )

    def issue_guest_access(self, *, guest_id: UUID, session_id: UUID) -> IssuedAccess:
        """Токен гостя (D5): без прав и плана, ``gst = true``; срок — как у пользователя."""
        return self._sign(
            {
                "sub": str(guest_id),
                "sid": str(session_id),
                "roles": [GUEST_ROLE],
                "perms": [],
                "gst": True,
            }
        )

    def _sign(self, subject_claims: dict[str, Any]) -> IssuedAccess:
        key = self._keys.active
        now = self._clock.now()
        claims = {
            **subject_claims,
            "iss": self._issuer,
            "aud": self._audience,
            "jti": str(uuid7()),
            "iat": now,
            "exp": now + timedelta(seconds=self._ttl),
        }
        token = jwt.encode(
            claims, key.private_key, algorithm=key.algorithm, headers={"kid": key.kid}
        )
        return IssuedAccess(token=token, expires_in=self._ttl)

    def issue_service(
        self, *, client_id: str, audience: str, scopes: Iterable[str], ttl_s: int
    ) -> IssuedServiceToken:
        """Сервисный токен (D13): ``sub = svc:<client_id>``, ``aud`` — вызываемый сервис.

        Только ``iss``, ``aud``, ``sub``, ``scope``, ``iat``, ``exp``, ``jti``: без ``sid``,
        ``roles``, ``perms`` — потребители отвергают сервисный токен с ними.
        """
        if audience == self._audience:
            # Защита в глубину: конфиг клиентов это уже запрещает.
            raise ValueError("сервисный токен не выдаётся на aud пользовательских токенов")
        key = self._keys.active
        now = self._clock.now()
        jti = str(uuid7())
        claims = {
            "iss": self._issuer,
            "aud": audience,
            "sub": f"{SERVICE_SUBJECT_PREFIX}{client_id}",
            "scope": " ".join(sorted(scopes)),
            "jti": jti,
            "iat": now,
            "exp": now + timedelta(seconds=ttl_s),
        }
        token = jwt.encode(
            claims, key.private_key, algorithm=key.algorithm, headers={"kid": key.kid}
        )
        return IssuedServiceToken(token=token, expires_in=ttl_s, token_id=jti)
