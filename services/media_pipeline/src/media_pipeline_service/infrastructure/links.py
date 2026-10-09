"""Подпись ссылок для nginx ``secure_link`` (ADR 0012), лимит выдачи в Redis, токены ключей."""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime
from urllib.parse import quote

from redis.asyncio import Redis
from redis.exceptions import RedisError

from gv_core.errors import DependencyUnavailableError


def md5_b64url(text: str) -> str:
    """``base64url(md5(text))`` без ``=`` — формат аргумента ``md5`` у nginx secure_link."""
    digest = hashlib.md5(text.encode(), usedforsecurity=False).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


class NginxSecureLinkSigner:
    """Ссылка вида ``<base><prefix><key>?md5=<token>&expires=<unix>``.

    На gateway ей соответствует::

        location /media/ {
            secure_link $arg_md5,$arg_expires;
            secure_link_md5 "$secure_link_expires$uri <secret>";
            if ($secure_link = "")  { return 403; }
            if ($secure_link = "0") { return 403; }   # истекла
            ...
        }

    Подписывается ``$uri`` — путь без query, как его видит nginx.
    """

    def __init__(
        self, secret: str, *, public_base_url: str = "", path_prefix: str = "/media/"
    ) -> None:
        if len(secret) < 32:  # noqa: PLR2004 — минимум энтропии секрета, не настройка
            raise ValueError("секрет secure_link должен быть не короче 32 символов")
        self._secret = secret
        self._base = public_base_url.rstrip("/")
        self._prefix = path_prefix

    def token(self, uri: str, expires: int) -> str:
        """Токен для ``$uri`` и срока ``expires`` (unix-секунды)."""
        return md5_b64url(f"{expires}{uri} {self._secret}")

    def sign(self, object_key: str, expires_at: datetime) -> str:
        """Подписанный URL объекта ассета."""
        uri = f"{self._prefix}{quote(object_key, safe='/.')}"
        expires = int(expires_at.timestamp())
        return f"{self._base}{uri}?md5={self.token(uri, expires)}&expires={expires}"


class UnconfiguredSigner:
    """Заглушка без секрета: выдача ссылок недоступна (503), остальной сервис работает."""

    def sign(self, object_key: str, expires_at: datetime) -> str:  # noqa: ARG002 — порт
        """Всегда отказ."""
        raise DependencyUnavailableError(
            "подпись ссылок не настроена (GV_MEDIA__SIGNING_SECRET)",
            code="asset.signer_unavailable",
        )


def limiter_unavailable(retry_after_s: int, message: str) -> DependencyUnavailableError:
    """503 лимитера ссылок; ``details.retry_after`` превращается в заголовок ``Retry-After``."""
    return DependencyUnavailableError(message, details={"retry_after": retry_after_s})


class RedisLinkRateLimiter:
    """Фиксированное окно: ``INCR`` + ``EXPIRE`` при первом попадании (атомарно в MULTI).

    Отказ Redis — fail-closed (503 с ``details.retry_after``): лимит заменяет
    одноразовость ссылок (ADR 0012), без него выдачу ограничить нечем.
    """

    def __init__(self, redis: Redis, *, retry_after_s: int, prefix: str = "media:linkrl:") -> None:
        self._redis = redis
        self._prefix = prefix
        self._retry_after_s = retry_after_s

    async def hit(self, key: str, window_s: int) -> int:
        """Учесть выдачу и вернуть счётчик окна."""
        full = f"{self._prefix}{key}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(full)
                pipe.expire(full, window_s, nx=True)
                count, _ = await pipe.execute()
        except RedisError as exc:
            raise limiter_unavailable(self._retry_after_s, "лимитер ссылок недоступен") from exc
        return int(count)


class SecretsTokenSource:
    """128 бит из ``secrets`` для ключей объектов."""

    def token_hex(self) -> str:
        """32 hex-символа."""
        return secrets.token_hex(16)
