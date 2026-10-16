"""Чтение медиатеки: полки, страница полки, полки единицы (план этапа 3b §6, L5).

Чтение не создаёт системных полок: несозданная полка показывается пустой
(``shelf_id: null``), лениво она появляется при первом сохранении.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from gv_core.errors import InvalidInputError
from gv_core.pagination import decode_cursor, encode_cursor
from library_service.application.ports import LibraryUnitOfWork, UnitOfWorkFactory
from library_service.application.views import (
    Json,
    custom_shelf_out,
    item_out,
    shelf_out,
    system_shelf_out,
)
from library_service.domain.library import LibraryLimits, UserLibrary
from library_service.domain.shelves import ShelfCatalog, ShelfKind, ShelfRef


def _bad_cursor() -> InvalidInputError:
    return InvalidInputError("неверный курсор", code="pagination.bad_cursor")


def _decode(cursor: str) -> tuple[datetime, UUID]:
    data = decode_cursor(cursor)
    added, item = data.get("a"), data.get("m")
    if not isinstance(added, str) or not isinstance(item, str):
        raise _bad_cursor()
    try:
        return datetime.fromisoformat(added), UUID(item)
    except ValueError as exc:
        raise _bad_cursor() from exc


class LibraryQueries:
    """Запросы публичного API.

    Args:
        uow: Фабрика Unit of Work.
        catalog: Конфиг полок.
        limits: Лимиты (агрегат строится для разбора ссылок и порядка показа).
    """

    def __init__(
        self, uow: UnitOfWorkFactory, catalog: ShelfCatalog, limits: LibraryLimits
    ) -> None:
        self._uow = uow
        self._catalog = catalog
        self._limits = limits

    async def shelves(self, user_id: UUID) -> list[Json]:
        """Системные полки (по конфигу, даже несозданные), затем свои."""
        async with self._uow() as uow:
            lib = await self._library(uow, user_id)
        system = [system_shelf_out(spec, lib.by_key(spec.key)) for spec in self._catalog.system]
        custom = [
            custom_shelf_out(self._catalog, s) for s in lib.visible() if s.kind is ShelfKind.CUSTOM
        ]
        return system + custom

    async def items(self, user_id: UUID, shelf_ref: str, cursor: str | None, limit: int) -> Json:
        """Страница полки (``ShelfItemsPage``).

        Raises:
            ShelfNotFoundError: полки нет или она чужая.
            InvalidInputError: ``pagination.bad_cursor``.
        """
        ref = ShelfRef.parse(shelf_ref)
        after = _decode(cursor) if cursor is not None else None
        async with self._uow() as uow:
            lib = await self._library(uow, user_id)
            shelf, spec = lib.resolve(ref)
            rows = await uow.library.page(shelf.id, after, limit + 1) if shelf is not None else []
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            last = page[-1].item
            next_cursor = encode_cursor(
                {"a": last.added_at.isoformat(), "m": str(last.media_item_id)}
            )
        return {
            "shelf": shelf_out(self._catalog, spec, shelf),
            "items": [item_out(self._catalog, r.item, r.entry) for r in page],
            "next_cursor": next_cursor,
        }

    async def item_shelves(self, user_id: UUID, media_item_id: UUID) -> Json:
        """На каких полках лежит единица (``ItemShelvesOut``)."""
        async with self._uow() as uow:
            lib = await self._library(uow, user_id, media_item_id)
        return {
            "media_item_id": str(media_item_id),
            "shelves": [str(r) for r in lib.refs_with(media_item_id)],
        }

    async def _library(
        self, uow: LibraryUnitOfWork, user_id: UUID, media_item_id: UUID | None = None
    ) -> UserLibrary:
        shelves = await uow.library.shelves(user_id)
        items = (
            await uow.library.placements(user_id, media_item_id)
            if media_item_id is not None
            else []
        )
        return UserLibrary(user_id, 0, shelves, items, catalog=self._catalog, limits=self._limits)
