"""Проверка access JWT, выпущенных сервисом identity.

Проверка локальная — по публичным ключам из JWKS, без запроса в identity на каждый вызов.
Ключи кэшируются; при неизвестном ``kid`` (ротация ключей) JWKS перезагружается, но не чаще
``min_refresh_interval``: подставной ``kid`` в токене не может заставить нас долбить identity.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import UUID

import httpx
import jwt
from jwt import PyJWK

from gv_auth.principal import Principal, Sanction
from gv_auth.revocation import RevocationChecker
from gv_core.errors import DependencyUnavailableError, UnauthenticatedError

# stdlib logging: gv_auth не зависит от structlog; записи всё равно проходят общий
# конвейер gv_observability (маскировка, correlation_id).
log = logging.getLogger(__name__)

ALLOWED_ALGORITHMS = ("EdDSA", "ES256")
"""Только асимметричные алгоритмы. ``none`` и HS* запрещены: HS* позволил бы любому сервису,
знающему секрет, выпускать токены, а путаница алгоритмов — классическая уязвимость JWT."""

JwksLoader = Callable[[], Awaitable[Mapping[str, Any]]]


def http_jwks_loader(url: str, *, timeout_s: float = 3.0) -> JwksLoader:
    """Загрузчик JWKS по HTTP."""

    async def load() -> Mapping[str, Any]:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(url)
            response.raise_for_status()
            data: Mapping[str, Any] = response.json()
            return data

    return load


class JwtVerifier:
    """Проверка подписи и claims access-токена."""

    def __init__(
        self,
        loader: JwksLoader,
        *,
        issuer: str,
        audience: str,
        leeway_s: int = 30,
        cache_ttl_s: float = 3600,
        min_refresh_interval_s: float = 30,
        clock: Callable[[], float] = time.monotonic,
        revocation: RevocationChecker | None = None,
        on_revocation_unavailable: Callable[[], None] | None = None,
    ) -> None:
        """Создать верификатор.

        Args:
            revocation: Проверка отзыва сессии по claim ``sid``. Если хранилище недоступно,
                токен принимается (fail-open): иначе отказ Redis выключил бы вход во всех
                сервисах. Окно риска ограничено TTL access-токена (15 минут).
            on_revocation_unavailable: Вызывается при каждом fail-open (метрика сервиса).
        """
        self._revocation = revocation
        self._on_revocation_unavailable = on_revocation_unavailable
        self._loader = loader
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway_s
        self._ttl = cache_ttl_s
        self._min_refresh = min_refresh_interval_s
        self._clock = clock
        self._keys: dict[str, PyJWK] = {}
        self._loaded_at: float | None = None
        # Время последней ПОПЫТКИ загрузки (успешной или нет). Троттлинг считается от неё:
        # иначе при недоступном identity каждый запрос после истечения TTL ждал бы таймаут.
        self._last_attempt: float | None = None
        self._lock = asyncio.Lock()

    async def verify(self, token: str) -> Principal:
        """Проверить токен и вернуть субъекта.

        Raises:
            UnauthenticatedError: токен отсутствует, подделан, просрочен или не для нас.
            DependencyUnavailableError: ключи нельзя получить, а кэша нет.
        """
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise UnauthenticatedError("некорректный токен", code="auth.token_malformed") from exc

        if header.get("alg") not in ALLOWED_ALGORITHMS:
            raise UnauthenticatedError("недопустимый алгоритм подписи", code="auth.token_invalid")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise UnauthenticatedError("в токене нет kid", code="auth.token_invalid")

        key = await self._key(kid)
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(ALLOWED_ALGORITHMS),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise UnauthenticatedError("токен истёк", code="auth.token_expired") from exc
        except jwt.PyJWTError as exc:
            raise UnauthenticatedError(
                "токен не прошёл проверку", code="auth.token_invalid"
            ) from exc
        principal = _principal_from_claims(claims)
        if principal.session_id is not None and await self._is_revoked(principal.session_id):
            raise UnauthenticatedError("сессия завершена", code="auth.session_revoked")
        return principal

    async def _is_revoked(self, session_id: str) -> bool:
        if self._revocation is None:
            return False
        try:
            return await self._revocation.is_revoked(session_id)
        except Exception as exc:
            # Fail-open — осознанное решение (services/identity/CLAUDE.md, «Особенности»).
            log.warning("auth.revocation_check_failed error=%s", type(exc).__name__)
            if self._on_revocation_unavailable is not None:
                self._on_revocation_unavailable()
            return False

    async def _key(self, kid: str) -> PyJWK:
        now = self._clock()
        expired = self._loaded_at is None or now - self._loaded_at > self._ttl
        if kid in self._keys and not expired:
            return self._keys[kid]
        async with self._lock:
            now = self._clock()
            expired = self._loaded_at is None or now - self._loaded_at > self._ttl
            may_refresh = (
                self._last_attempt is None or now - self._last_attempt >= self._min_refresh
            )
            if (expired or kid not in self._keys) and may_refresh:
                await self._refresh()
        if kid not in self._keys:
            raise UnauthenticatedError("неизвестный ключ подписи", code="auth.token_invalid")
        return self._keys[kid]

    async def _refresh(self) -> None:
        self._last_attempt = self._clock()
        try:
            jwks = await self._loader()
        except Exception as exc:
            if self._keys:
                # Identity недоступен, но кэш есть — продолжаем на старых ключах.
                return
            raise DependencyUnavailableError("ключи подписи недоступны") from exc
        keys: dict[str, PyJWK] = {}
        for raw in jwks.get("keys", []):
            kid = raw.get("kid")
            if not kid or raw.get("use", "sig") != "sig":
                continue
            try:
                keys[kid] = PyJWK(dict(raw))
            except jwt.PyJWTError:
                continue  # неподдерживаемый ключ пропускаем, остальные используем
        if not keys:
            # Пустой/битый JWKS не должен разом инвалидировать все действующие токены.
            if self._keys:
                return
            raise DependencyUnavailableError("JWKS не содержит пригодных ключей подписи")
        self._keys = keys
        self._loaded_at = self._clock()


def _principal_from_claims(claims: Mapping[str, Any]) -> Principal:
    try:
        user_id = UUID(str(claims["sub"]))
    except ValueError as exc:
        raise UnauthenticatedError("некорректный subject", code="auth.token_invalid") from exc
    try:
        sanctions = Sanction(int(claims.get("sanctions", 0)) & 0xFFFF)
    except (TypeError, ValueError) as exc:
        raise UnauthenticatedError("некорректные санкции", code="auth.token_invalid") from exc
    return Principal(
        user_id=user_id,
        roles=frozenset(_str_list(claims.get("roles"))),
        perms=frozenset(_str_list(claims.get("perms"))),
        sanctions=sanctions,
        plan=claims.get("plan") if isinstance(claims.get("plan"), str) else None,
        token_id=claims.get("jti") if isinstance(claims.get("jti"), str) else None,
        session_id=claims.get("sid") if isinstance(claims.get("sid"), str) else None,
    )


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str)]
