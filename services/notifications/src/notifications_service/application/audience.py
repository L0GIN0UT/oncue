"""Массовая доставка in-app уведомления аудитории (``audience`` типа, ADR 0054).

Событие ``progression.EventRunStarted`` порождает одну задачу RabbitMQ; воркер идёт по
аудитории ``known_users`` пачками по ``batch_size`` (keyset по ``user_id``), каждая пачка -
отдельная короткая транзакция. Идемпотентность - ``UNIQUE(user_id, dedup_key)``
(``event_started:{run_id}``): повтор задачи и рестарт середины рассылки второй записи не создают.
Игрок, отключивший тип, не получает уведомления. E-mail не используется.

Выбор: порта «все пользователи» (список из identity) нет, поэтому аудитория - игроки, о которых
знает сам notifications (есть уведомления или настройки). Игроки без единого уведомления
получат его после первого собственного; расширение аудитории - новый адаптер, не код типов.

Остановка: ``progression.EventRunFinished`` (``ended``/``stopped``) запоминается в
``finished_event_runs``; каждая пачка в начале своей транзакции проверяет, что запуск (``run_id``
из payload) не завершён, иначе воркер выходит (``notifications.fanout_cancelled``), уже
доставленное остаётся. Событие, пришедшее между проверкой и записью пачки, пачку не отменит -
пачка короткая (``batch_size``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

import structlog
from prometheus_client import Counter

from gv_core.clock import Clock
from notifications_service.application.ingest import store_candidate
from notifications_service.application.ports import UnitOfWorkFactory
from notifications_service.domain.catalog import TypeCatalog
from notifications_service.domain.mapping import map_event, matches_filter
from notifications_service.domain.notification import Notification

log = structlog.get_logger(__name__)


class CommitPublisher(Protocol):
    """Публикация в realtime после коммита сессии (``PostCommitDispatcher``)."""

    def schedule(self, session: Any, notifications: list[Notification]) -> None:  # Any: сессия БД
        """Опубликовать после коммита ``session``."""
        ...


FANOUT_TASK = "notifications.audience.fanout"
FANOUT_CREATED = Counter("gv_notifications_fanout_total", "Уведомления массовой доставки", ["type"])


RUN_ID_FIELD = "run_id"


def _run_id(payload: Mapping[str, Any]) -> UUID | None:
    raw = payload.get(RUN_ID_FIELD)
    try:
        return UUID(str(raw)) if raw is not None else None
    except ValueError:
        return None


class RecordRunFinished:
    """Запоминает завершение запуска события (идемпотентно по ``run_id``)."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow = uow_factory

    async def execute(self, run_id: UUID, reason: str, at: datetime) -> None:
        """Записать завершение; повтор ничего не меняет."""
        async with self._uow() as uow:
            await uow.finished_runs.mark(run_id, reason, at)
            await uow.commit()


class FanOutAudience:
    """Создаёт уведомление каждому игроку аудитории типа."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: TypeCatalog,
        clock: Clock,
        new_id: Callable[[], UUID],
        dispatcher: CommitPublisher,
        *,
        batch_size: int,
    ) -> None:
        self._uow = uow_factory
        self._catalog = catalog
        self._clock = clock
        self._new_id = new_id
        self._dispatcher = dispatcher
        self._batch = batch_size

    def audience_types(self, event_type: str, payload: Mapping[str, Any]) -> list[str]:
        """Ключи типов с аудиторией, подходящих под событие."""
        return [
            t.key
            for t in self._catalog.for_event(event_type)
            if t.audience is not None and matches_filter(t.filter, payload)
        ]

    async def execute(self, type_key: str, payload: Mapping[str, Any]) -> int:
        """Разослать по всей аудитории (повтор безопасен).

        Returns:
            Сколько уведомлений создано этим запуском.
        """
        notification_type = self._catalog.by_key(type_key)
        if notification_type is None or notification_type.audience is None:
            raise ValueError(f"тип {type_key} не с аудиторией")
        total = 0
        cursor: UUID | None = None
        run_id = _run_id(payload)
        while True:
            async with self._uow() as uow:
                if run_id is not None and await uow.finished_runs.is_finished(run_id):
                    log.info("notifications.fanout_cancelled", run_id=str(run_id), delivered=total)
                    return total
                users = await uow.notifications.known_users_after(cursor, self._batch)
                if not users:
                    return total
                cursor = users[-1]
                created = []
                for user_id in users:
                    candidate = map_event(
                        notification_type,
                        payload,
                        locale=self._catalog.default_locale,
                        default_locale=self._catalog.default_locale,
                        user_id=user_id,
                    )
                    notification = await store_candidate(
                        uow, notification_type, candidate, self._clock.now(), self._new_id()
                    )
                    if notification is not None:
                        created.append(notification)
                session = getattr(uow, "session", None)
                if session is not None:
                    self._dispatcher.schedule(session, created)
                await uow.commit()
            total += len(created)
            FANOUT_CREATED.labels(type_key).inc(len(created))
            log.info("notifications.fanout_batch", type=type_key, created=len(created))
