"""Centrifugo v6: публикация в каналы комнат и subscription-токены (R5, D9, ADR 0035).

Тонкий httpx-адаптер server API (``POST {api_url}/publish``, заголовок ``X-API-Key``) — без
клиентской библиотеки (``boring-by-default``: один вызов). Таймаут — ``GV_REALTIME__
TIMEOUT_S``; ретраев нет: повтор сообщения с той же версией клиенту не нужен, а пропуск он
заметит по версии и ресинкнется через ``GET /state`` или ``tick``. Ошибка публикации не
роняет владельца комнаты (лог и метрика).

Subscription-токен канала ``room:{code}`` — HS256 на отдельном секрете подписок
(``GV_REALTIME__SUBSCRIPTION_TOKEN_SECRET``): ``sub`` — пользователь, ``channel``, ``aud``,
``iat``, ``exp``. Секрет и токены не логируются.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import jwt
import structlog
from prometheus_client import Counter

from gv_service import RealtimeSettings
from match_service.domain.errors import MatchUnavailableError

log = structlog.get_logger(__name__)

REALTIME_PUBLISH = Counter("gv_match_realtime_publish_total", "Публикации в Centrifugo", ["result"])
UNSUBSCRIBE_FAILED = Counter(
    "gv_match_unsubscribe_failed_total", "Отзыв подписки Centrifugo не удался после всех попыток"
)


class CentrifugoPublisher:
    """``RealtimePublisher`` поверх server API Centrifugo."""

    def __init__(
        self,
        settings: RealtimeSettings,
        client: httpx.AsyncClient | None = None,
        *,
        unsubscribe_attempts: int = 1,
        unsubscribe_retry_s: float = 0.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if settings.api_url is None or settings.api_key is None:
            raise ValueError("нужны GV_REALTIME__API_URL и GV_REALTIME__API_KEY")
        if unsubscribe_attempts < 1:
            raise ValueError("unsubscribe_attempts должен быть >= 1")
        self._unsubscribe_attempts = unsubscribe_attempts
        self._unsubscribe_retry_s = unsubscribe_retry_s
        self._sleep = sleep
        self._url = settings.api_url.rstrip("/") + "/publish"
        self._unsubscribe_url = settings.api_url.rstrip("/") + "/unsubscribe"
        self._headers = {"X-API-Key": settings.api_key.get_secret_value()}
        self._client = client or httpx.AsyncClient(timeout=settings.timeout_s)

    async def aclose(self) -> None:
        """Закрыть HTTP-клиент."""
        await self._client.aclose()

    async def publish(self, channel: str, message: dict[str, Any]) -> None:  # Any: JSON
        """Опубликовать; отказ — лог и метрика (клиент ресинкнется по версии)."""
        try:
            response = await self._client.post(
                self._url, json={"channel": channel, "data": message}, headers=self._headers
            )
        except httpx.HTTPError as exc:
            REALTIME_PUBLISH.labels("network").inc()
            log.warning("match.realtime_unavailable", error_type=type(exc).__name__)
            return
        if response.status_code != httpx.codes.OK:
            REALTIME_PUBLISH.labels("http_error").inc()
            log.warning("match.realtime_rejected", status=response.status_code)
            return
        body = response.json() if response.content else {}
        if isinstance(body, dict) and body.get("error"):
            REALTIME_PUBLISH.labels("api_error").inc()
            log.warning("match.realtime_api_error", code=body["error"].get("code"))
            return
        REALTIME_PUBLISH.labels("ok").inc()

    async def unsubscribe(self, user_id: UUID, channel: str) -> None:
        """Отписать пользователя от канала (``POST /api/unsubscribe``) с повтором.

        До ``unsubscribe_attempts`` попыток с линейной паузой ``unsubscribe_retry_s``; каждая
        неудача считается в ``gv_match_realtime_publish_total``, исчерпание попыток - в
        ``gv_match_unsubscribe_failed_total`` и лог ``match.realtime_unsubscribe_gave_up``
        (хвост 4a, ADR 0052). Исключение не бросается: подписка всё равно умрёт вместе с
        subscription-токеном, а новый токен при ``ONLINE``-санкции не выдаётся.
        """
        for attempt in range(1, self._unsubscribe_attempts + 1):
            if await self._unsubscribe_once(user_id, channel):
                return
            if attempt < self._unsubscribe_attempts:
                await self._sleep(self._unsubscribe_retry_s * attempt)
        UNSUBSCRIBE_FAILED.inc()
        log.error(
            "match.realtime_unsubscribe_gave_up",
            channel=channel,
            attempts=self._unsubscribe_attempts,
        )

    async def _unsubscribe_once(self, user_id: UUID, channel: str) -> bool:
        """Одна попытка отзыва; ``True`` - успех."""
        try:
            response = await self._client.post(
                self._unsubscribe_url,
                json={"user": str(user_id), "channel": channel},
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            REALTIME_PUBLISH.labels("unsubscribe_network").inc()
            log.warning("match.realtime_unsubscribe_failed", error_type=type(exc).__name__)
            return False
        if response.status_code != httpx.codes.OK:
            REALTIME_PUBLISH.labels("unsubscribe_error").inc()
            log.warning("match.realtime_unsubscribe_rejected", status=response.status_code)
            return False
        # Centrifugo отвечает 200 и при ошибке API: она — в теле (``{"error": {...}}``).
        body = response.json() if response.content else {}
        if isinstance(body, dict) and body.get("error"):
            REALTIME_PUBLISH.labels("unsubscribe_api_error").inc()
            log.warning("match.realtime_unsubscribe_api_error", code=body["error"].get("code"))
            return False
        REALTIME_PUBLISH.labels("unsubscribe_ok").inc()
        return True


class NullPublisher:
    """Centrifugo не настроен (``GV_REALTIME__*`` нет): матч идёт на HTTP-tick (D10)."""

    async def publish(self, channel: str, message: dict[str, Any]) -> None:  # noqa: ARG002 — порт
        """Ничего не делает."""
        REALTIME_PUBLISH.labels("disabled").inc()

    async def unsubscribe(self, user_id: UUID, channel: str) -> None:
        """Подписок нет — отзывать нечего."""


class SubscriptionTokenIssuer:
    """``SubscriptionTokens``: JWT подписки на канал комнаты (D9)."""

    def __init__(self, settings: RealtimeSettings | None) -> None:
        secret = settings.subscription_token_secret if settings is not None else None
        self._secret = secret.get_secret_value() if secret is not None else None
        self._audience = settings.token_audience if settings is not None else ""
        self._ttl = timedelta(seconds=settings.token_ttl_s if settings is not None else 0)

    def issue(self, user_id: UUID, channel: str, now: datetime) -> tuple[str, datetime]:
        """Токен и момент истечения.

        Raises:
            MatchUnavailableError: секрет подписок не задан (realtime выключен).
        """
        if self._secret is None:
            raise MatchUnavailableError("realtime не настроен", code="match.realtime_unavailable")
        expires = now + self._ttl
        claims = {
            "sub": str(user_id),
            "channel": channel,
            "aud": self._audience,
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
        }
        return jwt.encode(claims, self._secret, algorithm="HS256"), expires
