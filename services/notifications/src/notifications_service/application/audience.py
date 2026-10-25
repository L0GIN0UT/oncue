"""Массовая доставка in-app уведомления аудитории (``audience`` типа, ADR 0054, 0069).

Событие ``progression.EventRunStarted`` порождает одну задачу RabbitMQ; воркер идёт по
аудитории пачками по ``batch_size``, каждая пачка - отдельная короткая транзакция записи.
Аудитории:

* ``active_users`` - все активные пользователи: страницы идентификаторов по курсору из identity
  (``UserDirectory``, internal-ручка, только id); страница читается вне транзакции БД;
* ``known_users`` - игроки, о которых знает сам notifications (keyset по ``user_id``).

Идемпотентность - ``UNIQUE(user_id, dedup_key)`` (``event_started:{run_id}``): повтор задачи и
рестарт середины рассылки второй записи не создают. Настройки игроков пачки читаются одним
запросом (``preferences.for_users``). Игрок, отключивший тип, не получает уведомления; тип,
выключенный админом, не рассылается (проверка в начале каждой пачки, ADR 0066). E-mail не
используется. Между пачками - пауза (``pause_s``).

Сбой identity: чтение страницы повторяется до ``page_retries`` раз с паузой ``retry_pause_s``;
если не вышло, исключение уходит из задачи и её повторяет очередь (доставленные пачки
остаются, повтор их пропускает по ``UNIQUE``). Событие целиком не теряется: оно уже превращено
в задачу. Окончательный отказ identity (настройка, права) - ``PermanentError`` выше по стеку.

Остановка: ``progression.EventRunFinished`` (``ended``/``stopped``) запоминается в
``finished_event_runs``; каждая пачка в начале проверяет, что запуск (``run_id`` из payload) не
завершён, иначе воркер выходит (``notifications.fanout_cancelled``), уже доставленное остаётся.
Событие, пришедшее между проверкой и записью пачки, пачку не отменит - пачка короткая.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import structlog
from prometheus_client import Counter, Histogram

from gv_core.clock import Clock
from gv_core.errors import DependencyUnavailableError
from notifications_service.application.ingest import store_candidate
from notifications_service.application.ports import (
    PERMANENT_DIRECTORY_CODES,
    UnitOfWorkFactory,
    UserDirectory,
    UserPage,
)
from notifications_service.application.type_switch import TypeSwitch
from notifications_service.domain.catalog import NotificationType, TypeCatalog
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
USER_DATA_ERASED = Counter(
    "gv_notifications_user_erased_total", "Обработано identity.UserDeleted (данные игрока удалены)"
)
FANOUT_PAGES = Counter(
    "gv_notifications_fanout_pages_total",
    "Чтение страниц аудитории из identity: ok, retry (повтор в задаче), failed (отказ задачи)",
    ["result"],
)
FANOUT_BATCH_SECONDS = Histogram(
    "gv_notifications_fanout_batch_seconds", "Запись одной пачки массовой доставки"
)


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


Sleep = Callable[[float], Awaitable[None]]

KNOWN_USERS = "known_users"
ACTIVE_USERS = "active_users"


class EraseUser:
    """Удаляет данные игрока по ``identity.UserDeleted`` (идемпотентно: повтор удаляет 0)."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow_factory
        self._clock = clock

    async def execute(self, user_id: UUID) -> int:
        """Стереть уведомления, настройки, согласие, устройства и доставки; вернуть число строк.

        В той же транзакции пишется tombstone: пачка массовой доставки, читавшая страницу id
        до удаления, игроку уведомление не создаст (ADR 0069).
        """
        async with self._uow() as uow:
            await uow.erased_users.mark(user_id, self._clock.now())
            erased = await uow.user_data.erase(user_id)
            await uow.commit()
        USER_DATA_ERASED.inc()
        log.info("notifications.user_erased", rows=erased)
        return erased


class PurgeErasedUsers:
    """Чистит tombstone старше срока (он длиннее самой долгой рассылки)."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock, retention: timedelta) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._retention = retention

    async def execute(self) -> int:
        """Удалить устаревшие tombstone; вернуть их число (повтор безопасен)."""
        async with self._uow() as uow:
            purged = await uow.erased_users.purge_before(self._clock.now() - self._retention)
            await uow.commit()
        if purged:
            log.info("notifications.erased_purged", rows=purged)
        return purged


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
        switch: TypeSwitch,
        batch_size: int,
        users: UserDirectory,
        pause_s: float = 0.0,
        page_retries: int = 0,
        retry_pause_s: float = 0.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._switch = switch
        self._uow = uow_factory
        self._catalog = catalog
        self._clock = clock
        self._new_id = new_id
        self._dispatcher = dispatcher
        self._batch = batch_size
        self._users = users
        self._pause = pause_s
        self._retries = page_retries
        self._retry_pause = retry_pause_s
        self._sleep = sleep

    def audience_types(self, event_type: str, payload: Mapping[str, Any]) -> list[str]:
        """Ключи типов с аудиторией, подходящих под событие."""
        return [
            t.key
            for t in self._catalog.for_event(event_type)
            if t.audience is not None and matches_filter(t.filter, payload)
        ]

    async def _page(self, audience: str, cursor: UUID | None) -> UserPage:
        """Следующая страница аудитории; чтение identity повторяется при временном сбое."""
        if audience == KNOWN_USERS:
            async with self._uow() as uow:
                ids = await uow.notifications.known_users_after(cursor, self._batch)
            return UserPage(ids, ids[-1] if len(ids) == self._batch else None)
        attempt = 0
        while True:
            try:
                page = await self._users.active_after(cursor, self._batch)
            except DependencyUnavailableError as exc:
                if exc.code in PERMANENT_DIRECTORY_CODES or attempt >= self._retries:
                    FANOUT_PAGES.labels("failed").inc()
                    raise
                attempt += 1
                FANOUT_PAGES.labels("retry").inc()
                log.warning("notifications.fanout_page_retry", attempt=attempt, code=exc.code)
                await self._sleep(self._retry_pause)
                continue
            FANOUT_PAGES.labels("ok").inc()
            return page

    async def execute(self, type_key: str, payload: Mapping[str, Any]) -> int:
        """Разослать по всей аудитории (повтор безопасен).

        Returns:
            Сколько уведомлений создано этим запуском.

        Raises:
            DependencyUnavailableError: identity недоступен дольше повторов задачи (задачу
                повторит очередь) или отказал окончательно (код из ``PERMANENT_DIRECTORY_CODES``).
        """
        notification_type = self._catalog.by_key(type_key)
        if notification_type is None or notification_type.audience is None:
            raise ValueError(f"тип {type_key} не с аудиторией")
        audience = notification_type.audience
        total = 0
        cursor: UUID | None = None
        run_id = _run_id(payload)
        while True:
            if not await self._should_continue(notification_type, run_id, total):
                return total
            page = await self._page(audience, cursor)
            if not page.ids:
                return total
            created = await self._store_batch(notification_type, payload, page.ids)
            total += len(created)
            FANOUT_CREATED.labels(type_key).inc(len(created))
            log.info("notifications.fanout_batch", type=type_key, created=len(created))
            if page.next_cursor is None:
                return total
            cursor = page.next_cursor
            if self._pause > 0:
                await self._sleep(self._pause)

    async def _should_continue(
        self, notification_type: NotificationType, run_id: UUID | None, delivered: int
    ) -> bool:
        """Запуск не завершён и тип не выключен админом (проверка в начале каждой пачки)."""
        async with self._uow() as uow:
            if run_id is not None and await uow.finished_runs.is_finished(run_id):
                log.info("notifications.fanout_cancelled", run_id=str(run_id), delivered=delivered)
                return False
            if notification_type.admin_switchable and not await self._switch.is_enabled(
                uow, notification_type.key
            ):
                log.info(
                    "notifications.type_disabled", type=notification_type.key, delivered=delivered
                )
                return False
        return True

    async def _store_batch(
        self, notification_type: NotificationType, payload: Mapping[str, Any], user_ids: list[UUID]
    ) -> list[Notification]:
        started = time.perf_counter()
        created: list[Notification] = []
        async with self._uow() as uow:
            # Удалённые между чтением страницы и записью пачки: их не пишем (tombstone).
            erased = await uow.erased_users.among(user_ids)
            if erased:
                log.info("notifications.fanout_skipped_erased", count=len(erased))
                user_ids = [u for u in user_ids if u not in erased]
            preferences = (
                await uow.preferences.for_users(user_ids, notification_type.key)
                if notification_type.user_mutable
                else {}
            )
            for user_id in user_ids:
                candidate = map_event(
                    notification_type,
                    payload,
                    locale=self._catalog.default_locale,
                    default_locale=self._catalog.default_locale,
                    user_id=user_id,
                )
                notification = await store_candidate(
                    uow,
                    notification_type,
                    candidate,
                    self._clock.now(),
                    self._new_id(),
                    preferences=preferences,
                )
                if notification is not None:
                    created.append(notification)
            session = getattr(uow, "session", None)
            if session is not None:
                self._dispatcher.schedule(session, created)
            await uow.commit()
        FANOUT_BATCH_SECONDS.observe(time.perf_counter() - started)
        return created
