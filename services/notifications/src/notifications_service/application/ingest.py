"""Приём доменного события: событие -> уведомление игроку (ADR 0047)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog

from gv_core.clock import Clock
from notifications_service.application.ports import UnitOfWork, UnitOfWorkFactory
from notifications_service.domain.catalog import NotificationType, TypeCatalog
from notifications_service.domain.mapping import map_event, matches_filter
from notifications_service.domain.notification import Candidate, Notification

log = structlog.get_logger(__name__)


async def store_candidate(
    uow: UnitOfWork,
    notification_type: NotificationType,
    candidate: Candidate,
    now: datetime,
    new_id: UUID,
) -> Notification | None:
    """Сохранить уведомление с учётом настроек игрока.

    Returns:
        Созданное уведомление; ``None`` - игрок отключил тип или такая запись уже есть.
    """
    in_app = notification_type.channels.in_app
    if notification_type.user_mutable:
        pref = (await uow.preferences.all(candidate.user_id)).get(notification_type.key)
        if pref is not None:
            in_app = pref.in_app
    if not in_app:
        log.info("notifications.muted", type=notification_type.key)
        return None
    notification = Notification(
        id=new_id,
        user_id=candidate.user_id,
        type_key=candidate.type_key,
        dedup_key=candidate.dedup_key,
        title=candidate.title,
        body=candidate.body,
        data=candidate.data,
        created_at=now,
    )
    return notification if await uow.notifications.add(notification) else None


class IngestEvent:
    """Создаёт уведомления по событию, настроенному в конфиге типов.

    Идемпотентность - по ``(user_id, dedup_key)`` (предмет события), поэтому повтор события
    с новым ``event_id`` второй записи не создаёт. Игрок, отключивший in-app для типа с
    ``user_mutable``, уведомления не получает; тип с ``user_mutable = false`` отключить нельзя.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: TypeCatalog,
        clock: Clock,
        new_id: Callable[[], UUID],
    ) -> None:
        self._uow = uow_factory
        self._catalog = catalog
        self._clock = clock
        self._new_id = new_id

    async def execute(self, event_type: str, payload: Mapping[str, Any]) -> list[Notification]:
        """Сохранить уведомления по событию.

        Returns:
            Только вновь созданные уведомления (для realtime-доставки после коммита).

        Raises:
            MappingError: событие не соответствует контракту типа.
        """
        created: list[Notification] = []
        async with self._uow() as uow:
            for notification_type in self._catalog.for_event(event_type):
                if notification_type.audience is not None:
                    continue  # массовая доставка - задача FanOutAudience
                if not matches_filter(notification_type.filter, payload):
                    continue
                candidate = map_event(
                    notification_type,
                    payload,
                    locale=self._catalog.default_locale,
                    default_locale=self._catalog.default_locale,
                )
                notification = await store_candidate(
                    uow, notification_type, candidate, self._clock.now(), self._new_id()
                )
                if notification is not None:
                    created.append(notification)
            await uow.commit()
        return created
