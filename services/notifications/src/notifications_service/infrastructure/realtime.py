"""Публикация в личный канал Centrifugo (server API, ADR 0035, 0047).

Тонкий httpx-адаптер ``POST {api_url}/publish`` с ``X-API-Key``. Запись уведомления в БД
первична: отказ Centrifugo не теряет её и не роняет consumer - лог и метрика, клиент догонит
списком (``GET /v1/notifications``). Ключ API не логируется.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import httpx
import structlog
from prometheus_client import Counter
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from gv_service import RealtimeSettings
from notifications_service.application.ports import RealtimePublisher
from notifications_service.domain.notification import Notification

log = structlog.get_logger(__name__)

REALTIME_PUBLISH = Counter(
    "gv_notifications_realtime_publish_total", "Публикации в Centrifugo", ["result"]
)
PERSONAL_NAMESPACE = "personal"


def personal_channel(user_id: UUID) -> str:
    """Личный канал игрока: ``personal:#<user_id>`` (user-limited channel Centrifugo)."""
    return f"{PERSONAL_NAMESPACE}:#{user_id}"


class CentrifugoPublisher:
    """``RealtimePublisher`` поверх server API Centrifugo."""

    def __init__(self, settings: RealtimeSettings, client: httpx.AsyncClient | None = None) -> None:
        if settings.api_url is None or settings.api_key is None:
            raise ValueError("нужны GV_REALTIME__API_URL и GV_REALTIME__API_KEY")
        self._url = settings.api_url.rstrip("/") + "/publish"
        self._headers = {"X-API-Key": settings.api_key.get_secret_value()}
        self._client = client or httpx.AsyncClient(timeout=settings.timeout_s)

    async def aclose(self) -> None:
        """Закрыть HTTP-клиент."""
        await self._client.aclose()

    async def publish(self, channel: str, message: dict[str, Any]) -> None:  # Any: JSON
        """Опубликовать; любой отказ - лог и метрика, исключение наружу не уходит."""
        try:
            response = await self._client.post(
                self._url, json={"channel": channel, "data": message}, headers=self._headers
            )
        except httpx.HTTPError as exc:
            REALTIME_PUBLISH.labels("network").inc()
            log.warning("notifications.realtime_unavailable", error_type=type(exc).__name__)
            return
        if response.status_code != httpx.codes.OK:
            REALTIME_PUBLISH.labels("http_error").inc()
            log.warning("notifications.realtime_rejected", status=response.status_code)
            return
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {}
        # Centrifugo отвечает 200 и при ошибке API: она - в теле.
        if isinstance(body, dict) and body.get("error"):
            REALTIME_PUBLISH.labels("api_error").inc()
            log.warning("notifications.realtime_api_error", code=body["error"].get("code"))
            return
        REALTIME_PUBLISH.labels("ok").inc()


def realtime_message(notification: Notification) -> dict[str, Any]:  # Any: JSON
    """Сообщение личного канала: то же представление, что в ``GET /v1/notifications``."""
    return {
        "type": "notification.created",
        "notification": {
            "id": str(notification.id),
            "type": notification.type_key,
            "title": notification.title,
            "body": notification.body,
            "created_at": notification.created_at.isoformat(),
            "read_at": None,
        },
    }


class PostCommitDispatcher:
    """Публикует уведомления в личные каналы ПОСЛЕ коммита транзакции consumer.

    Запись первична: если транзакция откатилась, публиковать нечего; если Centrifugo
    недоступен, запись остаётся (адаптер не бросает). Задачи публикации не переживают остановку
    процесса - клиент догонит списком.
    """

    def __init__(self, publisher: RealtimePublisher) -> None:
        self._publisher = publisher
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(self, session: AsyncSession, notifications: list[Notification]) -> None:
        """Опубликовать после успешного коммита ``session``."""
        if not notifications:
            return
        loop = asyncio.get_running_loop()

        def on_commit(_: object) -> None:
            task = loop.create_task(self._publish(notifications))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        event.listen(session.sync_session, "after_commit", on_commit, once=True)

    async def _publish(self, notifications: list[Notification]) -> None:
        for notification in notifications:
            try:
                await self._publisher.publish(
                    personal_channel(notification.user_id), realtime_message(notification)
                )
            except Exception:
                REALTIME_PUBLISH.labels("exception").inc()
                log.warning("notifications.realtime_failed", exc_info=True)

    async def drain(self, timeout_s: float = 10.0) -> None:
        """Дождаться уже запущенных публикаций (остановка процесса, тесты)."""
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=timeout_s)


class NullPublisher:
    """Centrifugo не настроен: уведомления доступны списком."""

    async def publish(self, channel: str, message: dict[str, Any]) -> None:  # noqa: ARG002 — порт
        """Ничего не делает."""
        REALTIME_PUBLISH.labels("disabled").inc()
