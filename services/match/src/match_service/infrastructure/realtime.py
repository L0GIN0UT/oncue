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


class CentrifugoPublisher:
    """``RealtimePublisher`` поверх server API Centrifugo."""

    def __init__(self, settings: RealtimeSettings, client: httpx.AsyncClient | None = None) -> None:
        self._url = settings.api_url.rstrip("/") + "/publish"
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


class NullPublisher:
    """Centrifugo не настроен (``GV_REALTIME__*`` нет): матч идёт на HTTP-tick (D10)."""

    async def publish(self, channel: str, message: dict[str, Any]) -> None:  # noqa: ARG002 — порт
        """Ничего не делает."""
        REALTIME_PUBLISH.labels("disabled").inc()


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
