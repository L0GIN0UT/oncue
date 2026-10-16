"""Обработчики событий других сервисов (план этапа 3b §6, L4).

* ``catalog.MediaItem*`` → проекция ``catalog_items_proj``; порядок защищает ``version``:
  событие не новее применённого отбрасывается. Блокировка ставит статус, а не удаляет
  единицу с полок (D11).
* ``identity.UserDeleted`` → удаление всей медиатеки пользователя порциями: consumer
  ставит отметку и удаляет первую порцию, остальное дочищает периодическая задача.
  Повтор события и повтор задачи — no-op.
"""

from __future__ import annotations

from uuid import UUID

import structlog

from gv_core.clock import Clock
from library_service.application.ports import LibraryUnitOfWork, UnitOfWorkFactory
from library_service.domain.library import CatalogEntry, ItemStatus, is_newer

log = structlog.get_logger(__name__)


class ApplyCatalogEvent:
    """Проекция единиц каталога.

    Args:
        clock: Часы (``updated_at`` строки проекции).
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def snapshot(self, uow: LibraryUnitOfWork, entry: CatalogEntry) -> bool:
        """Полный снимок (``Created``/``Updated``); ``False`` — событие устарело."""
        current = await uow.projection.get(entry.media_item_id, for_update=True)
        if not is_newer(entry.version, current.version if current else None):
            log.info("library.catalog_event_stale", media_item_id=str(entry.media_item_id))
            return False
        await uow.projection.upsert(entry, self._clock.now())
        return True

    async def status(
        self, uow: LibraryUnitOfWork, media_item_id: UUID, status: ItemStatus, version: int
    ) -> bool:
        """Блокировка или разблокировка; ``False`` — устарело или единицы ещё нет."""
        current = await uow.projection.get(media_item_id, for_update=True)
        if current is None:
            # Снимок придёт событием Created (порядок по ключу партиции сохраняется).
            log.warning("library.catalog_status_without_item", media_item_id=str(media_item_id))
            return False
        if not is_newer(version, current.version):
            return False
        await uow.projection.set_status(media_item_id, status, version, self._clock.now())
        return True


class PurgeUser:
    """Удаление медиатеки пользователя после ``identity.UserDeleted``.

    Args:
        uow: Фабрика Unit of Work (для периодической задачи: порция — своя транзакция).
        clock: Часы.
        batch: Строк за одну порцию (``GV_LIBRARY__PURGE_BATCH``).
        users_per_run: Пользователей за один запуск задачи.
    """

    def __init__(
        self, uow: UnitOfWorkFactory, clock: Clock, *, batch: int, users_per_run: int
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._batch = batch
        self._users_per_run = users_per_run

    async def request(self, uow: LibraryUnitOfWork, user_id: UUID) -> int:
        """Отметить пользователя к удалению и удалить первую порцию (в транзакции consumer).

        Returns:
            Число удалённых строк.
        """
        await uow.purges.request(user_id, self._clock.now())
        deleted = await uow.purges.delete_batch(user_id, self._batch)
        log.info("library.user_purge_requested", deleted=deleted)
        return deleted

    async def run(self) -> int:
        """Дочистить отмеченных пользователей порциями (каждая — своя транзакция).

        Returns:
            Число удалённых строк за запуск.
        """
        async with self._uow() as uow:
            users = await uow.purges.pending(self._users_per_run)
        total = 0
        for user_id in users:
            while True:
                async with self._uow() as uow:
                    deleted = await uow.purges.delete_batch(user_id, self._batch)
                    if deleted == 0:
                        await uow.purges.finish(user_id)
                    await uow.commit()
                total += deleted
                if deleted == 0:
                    break
        if users:
            log.info("library.user_purge_run", users=len(users), deleted=total)
        return total
