"""Отзыв сессий: denylist идентификаторов сессий (``sid``) в Redis.

Identity при выходе, «выйти везде», повторном использовании refresh-токена и удалении
аккаунта кладёт ключ ``auth:revoked_sid:<sid>`` с TTL, равным времени жизни access-токена.
Любой сервис с доступом к тому же Redis проверяет ключ через ``RedisRevocationChecker``,
переданный в ``JwtVerifier(revocation=...)``. Формат ключа — общий контракт identity и
потребителей; менять только вместе.

Модуль не импортирует ``redis``: клиент описан протоколом, поэтому gv_auth не тянет
зависимость, а в тестах подходит любой объект с ``exists``.
"""

from __future__ import annotations

import inspect
from typing import Any, Protocol

REVOKED_SID_PREFIX = "auth:revoked_sid:"


def revoked_sid_key(session_id: str, *, prefix: str = "") -> str:
    """Ключ denylist для сессии. ``prefix`` — пространство имён (тесты на общем Redis)."""
    return f"{prefix}{REVOKED_SID_PREFIX}{session_id}"


class RevocationChecker(Protocol):
    """Проверка, отозвана ли сессия."""

    async def is_revoked(self, session_id: str) -> bool:
        """``True``, если сессия отозвана. Исключение — хранилище недоступно."""
        ...


class SupportsExists(Protocol):
    """Минимальный интерфейс клиента Redis (``redis.asyncio.Redis`` подходит)."""

    # Any: у redis-py тип ответа ``ResponseT = Awaitable[Any] | Any`` (sync и async клиенты).
    def exists(self, *names: str) -> Any:
        """Количество существующих ключей."""
        ...


class RedisRevocationChecker:
    """Проверка denylist ``sid`` в Redis."""

    def __init__(self, client: SupportsExists, *, prefix: str = "") -> None:
        self._client = client
        self._prefix = prefix

    async def is_revoked(self, session_id: str) -> bool:
        """Есть ли ключ отзыва для сессии."""
        result = self._client.exists(revoked_sid_key(session_id, prefix=self._prefix))
        if inspect.isawaitable(result):
            result = await result
        return bool(result)
