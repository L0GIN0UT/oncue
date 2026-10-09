"""Адаптеры Redis: лимитер, denylist сессий, одноразовые OAuth state.

Ключи принимают ``prefix`` — пространство имён (тесты на общем Redis, несколько стендов).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from uuid import UUID

import structlog
from prometheus_client import Counter
from redis.asyncio import Redis
from redis.exceptions import RedisError

from gv_auth.revocation import revoked_sid_key
from identity_service.application.ports import OAuthState, RateDecision
from identity_service.domain import Provider

log = structlog.get_logger(__name__)

REDIS_FAIL_OPEN = Counter(
    "identity_redis_fail_open_total",
    "Проверки, пропущенные из-за недоступного Redis (fail-open)",
    ["component"],
)


# DECR без ухода в минус и без создания ключа: прощать нечего — ничего не делаем.
_FORGIVE_LUA = """
local v = tonumber(redis.call('GET', KEYS[1]) or '0')
if v > 0 then return redis.call('DECR', KEYS[1]) end
return 0
"""


class RedisRateLimiter:
    """Фиксированное окно: ``INCR`` + ``EXPIRE NX``.

    При недоступном Redis — fail-open (с метрикой и логом): иначе отказ Redis выключил бы
    вход для всех. Перебор паролей при этом всё равно ограничен стоимостью argon2id.
    """

    def __init__(self, client: Redis, *, prefix: str = "") -> None:
        self._r, self._prefix = client, prefix
        self._forgive = client.register_script(_FORGIVE_LUA)

    async def hit(self, key: str, *, limit: int, window_s: int) -> RateDecision:
        """Засчитать попытку."""
        full = self._prefix + key
        try:
            async with self._r.pipeline(transaction=True) as pipe:
                pipe.incr(full)
                pipe.expire(full, window_s, nx=True)
                pipe.ttl(full)
                count, _, ttl = await pipe.execute()
        except RedisError as exc:
            return self._fail_open(exc)
        return RateDecision(int(count) <= limit, max(int(ttl), 1))

    async def forgive(self, key: str) -> None:
        """Вернуть одну попытку (атомарно, не ниже нуля); ошибки Redis — только в лог."""
        try:
            await self._forgive(keys=[self._prefix + key])
        except RedisError as exc:
            self._fail_open(exc)

    async def reset(self, key: str) -> None:
        """Обнулить счётчик; ошибки Redis — только в лог."""
        try:
            await self._r.delete(self._prefix + key)
        except RedisError as exc:
            self._fail_open(exc)

    @staticmethod
    def _fail_open(exc: Exception) -> RateDecision:
        REDIS_FAIL_OPEN.labels("rate_limit").inc()
        log.warning("auth.rate_limit_unavailable", error=type(exc).__name__)
        return RateDecision(allowed=True)


class RedisRevocationStore:
    """Запись ``auth:revoked_sid:<sid>`` (формат — gv_auth.revocation).

    ``ttl_s`` — время жизни access-токена ПЛЮС допуск (leeway) верификатора: токен
    принимается ещё leeway секунд после ``exp``, и запись должна его пережить.
    """

    def __init__(self, client: Redis, *, ttl_s: int, prefix: str = "") -> None:
        self._r, self._ttl, self._prefix = client, ttl_s, prefix

    async def revoke(self, session_ids: Iterable[UUID]) -> None:
        """Отозвать сессии (ошибку Redis пробрасывает — решает вызывающий)."""
        async with self._r.pipeline(transaction=False) as pipe:
            for sid in session_ids:
                pipe.set(revoked_sid_key(str(sid), prefix=self._prefix), b"1", ex=self._ttl)
            await pipe.execute()


class RedisOAuthStateStore:
    """Одноразовый state: ``SET NX EX`` и ``GETDEL`` (повторное использование невозможно)."""

    def __init__(self, client: Redis, *, prefix: str = "") -> None:
        self._r, self._prefix = client, prefix

    def _key(self, key: str) -> str:
        return f"{self._prefix}oauth:state:{key}"

    async def put(self, key: str, state: OAuthState, ttl_s: int) -> None:
        """Сохранить (значение содержит PKCE verifier — только в Redis, не в логах)."""
        payload = {
            "purpose": state.purpose,
            "provider": state.provider.value,
            "user_id": str(state.user_id) if state.user_id else None,
            "session_id": str(state.session_id) if state.session_id else None,
            "code_verifier": state.code_verifier,
            "browser_binding": state.browser_binding,
            "return_to": state.return_to,
        }
        await self._r.set(self._key(key), json.dumps(payload), ex=ttl_s, nx=True)

    async def pop(self, key: str) -> OAuthState | None:
        """Забрать и удалить."""
        raw = await self._r.getdel(self._key(key))
        if raw is None:
            return None
        data = json.loads(raw)
        return OAuthState(
            purpose=data["purpose"],
            provider=Provider(data["provider"]),
            user_id=UUID(data["user_id"]) if data["user_id"] else None,
            session_id=UUID(data["session_id"]) if data["session_id"] else None,
            code_verifier=data["code_verifier"],
            browser_binding=data["browser_binding"],
            return_to=data["return_to"],
        )
