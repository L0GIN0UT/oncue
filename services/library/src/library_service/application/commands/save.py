"""Мутации медиатеки: сохранение и удаление единиц, свои полки (план этапа 3b §6, L3).

Каждая мутация: лимит частоты (Redis, fail-open) → транзакция: ``Idempotency-Key`` →
блокировка медиатеки пользователя (``user_versions``) → агрегат ``UserLibrary`` →
изменения и события ``ItemSaved``/``ItemRemoved`` в outbox одной транзакцией. Повтор
без изменений событий не публикует (D12).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.ids import uuid7
from gv_db.idempotency import request_fingerprint, run_idempotent
from gv_text.ugc import UgcViolation
from library_service.application.ports import (
    LibraryUnitOfWork,
    RateLimiter,
    TitlePolicy,
    UnitOfWorkFactory,
)
from library_service.application.views import Json, custom_shelf_out, placement_out
from library_service.domain.errors import TitleInvalidError
from library_service.domain.library import LibraryLimits, ShelfItem, UserLibrary
from library_service.domain.shelves import SaveSource, ShelfCatalog, ShelfKind, ShelfRef

log = structlog.get_logger(__name__)

RATE_WINDOW_S = 60
BUCKET_ITEMS = "items"
BUCKET_SHELVES = "shelves"

type Mutation = Callable[[UserLibrary, LibraryUnitOfWork, datetime], Awaitable[Json]]
type ItemsLoader = Callable[[LibraryUnitOfWork, UserLibraryInput], Awaitable[list[ShelfItem]]]


@dataclass(frozen=True, slots=True)
class CommandLimits:
    """Лимиты мутаций (``GV_LIBRARY__LIMITS__*``)."""

    saves_per_min: int
    shelf_title_max: int
    library: LibraryLimits


@dataclass(frozen=True, slots=True)
class UserLibraryInput:
    """Что нужно для загрузки строк ``shelf_items`` агрегата."""

    user_id: UUID
    shelf_ids: frozenset[UUID]


class LibraryCommands:
    """Use-cases мутаций медиатеки.

    Args:
        uow: Фабрика Unit of Work.
        clock: Часы.
        catalog: Конфиг полок.
        limiter: Лимит частоты.
        policy: Проверка названий (``gv_text.ugc``).
        limits: Лимиты.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        *,
        catalog: ShelfCatalog,
        limiter: RateLimiter,
        policy: TitlePolicy,
        limits: CommandLimits,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._catalog = catalog
        self._limiter = limiter
        self._policy = policy
        self._limits = limits

    # ------------------------------------------------------------------ единицы
    async def save_item(
        self,
        user_id: UUID,
        shelf_ref: str,
        media_item_id: UUID,
        source: SaveSource,
        idempotency_key: str,
    ) -> Json:
        """Сохранить единицу на полку (``SaveResult``).

        Raises:
            ShelfNotFoundError, ItemUnavailableError, DomainMismatchError, ShelfFullError,
            LibraryRateLimitedError: см. ``UserLibrary.save`` и лимиты.
        """
        ref = ShelfRef.parse(shelf_ref)
        await self._hit(BUCKET_ITEMS, user_id)

        async def mutate(lib: UserLibrary, uow: LibraryUnitOfWork, now: datetime) -> Json:
            entry = await uow.projection.get(media_item_id)
            result = lib.save(ref, entry, source, now=now, new_shelf_id=uuid7())
            return placement_out(result)

        async def items(uow: LibraryUnitOfWork, inp: UserLibraryInput) -> list[ShelfItem]:
            return await uow.library.placements(inp.user_id, media_item_id)

        return await self._mutate(
            user_id,
            ("item.save", f"{ref}/{media_item_id}", {"source": str(source)}),
            idempotency_key,
            mutate,
            items,
        )

    async def remove_item(
        self,
        user_id: UUID,
        shelf_ref: str,
        media_item_id: UUID,
        source: SaveSource,
        idempotency_key: str,
    ) -> Json:
        """Убрать единицу с полки (``SaveResult``); повтор — ``changed: false``.

        Raises:
            ShelfNotFoundError, LibraryRateLimitedError.
        """
        ref = ShelfRef.parse(shelf_ref)
        await self._hit(BUCKET_ITEMS, user_id)

        async def mutate(lib: UserLibrary, uow: LibraryUnitOfWork, now: datetime) -> Json:  # noqa: ARG001 — сигнатура Mutation
            return placement_out(lib.remove(ref, media_item_id, source, now=now))

        async def items(uow: LibraryUnitOfWork, inp: UserLibraryInput) -> list[ShelfItem]:
            return await uow.library.placements(inp.user_id, media_item_id)

        return await self._mutate(
            user_id,
            ("item.remove", f"{ref}/{media_item_id}", {"source": str(source)}),
            idempotency_key,
            mutate,
            items,
        )

    # ------------------------------------------------------------------ свои полки
    async def create_shelf(self, user_id: UUID, title: str, idempotency_key: str) -> Json:
        """Своя полка (``ShelfOut``).

        Raises:
            TitleInvalidError, ShelvesLimitError, LibraryRateLimitedError.
        """
        clean = self._title(title)
        await self._hit(BUCKET_SHELVES, user_id)

        async def mutate(lib: UserLibrary, uow: LibraryUnitOfWork, now: datetime) -> Json:  # noqa: ARG001 — сигнатура Mutation
            shelf = lib.create_shelf(clean, now=now, new_shelf_id=uuid7())
            return custom_shelf_out(self._catalog, shelf)

        return await self._mutate(
            user_id, ("shelf.create", None, {"title": clean}), idempotency_key, mutate, None
        )

    async def rename_shelf(
        self, user_id: UUID, shelf_id: UUID, title: str, idempotency_key: str
    ) -> Json:
        """Переименовать свою полку (``ShelfOut``).

        Raises:
            ShelfNotFoundError, TitleInvalidError, LibraryRateLimitedError.
        """
        clean = self._title(title)
        await self._hit(BUCKET_SHELVES, user_id)

        async def mutate(lib: UserLibrary, uow: LibraryUnitOfWork, now: datetime) -> Json:  # noqa: ARG001 — сигнатура Mutation
            return custom_shelf_out(self._catalog, lib.rename_shelf(shelf_id, clean, now=now))

        return await self._mutate(
            user_id, ("shelf.rename", shelf_id, {"title": clean}), idempotency_key, mutate, None
        )

    async def delete_shelf(self, user_id: UUID, shelf_id: UUID, idempotency_key: str) -> None:
        """Удалить свою полку; её единицы — ``ItemRemoved`` (``source: shelf_deleted``).

        Raises:
            ShelfNotFoundError, LibraryRateLimitedError.
        """
        await self._hit(BUCKET_SHELVES, user_id)

        async def mutate(lib: UserLibrary, uow: LibraryUnitOfWork, now: datetime) -> Json:  # noqa: ARG001 — сигнатура Mutation
            removed = lib.delete_shelf(shelf_id, now=now)
            log.info("library.shelf_deleted", items=removed)
            return {}

        async def items(uow: LibraryUnitOfWork, inp: UserLibraryInput) -> list[ShelfItem]:
            # Строки читаются только для своей полки пользователя: чужой id → 404 в агрегате.
            if shelf_id not in inp.shelf_ids:
                return []
            return await uow.library.shelf_items(shelf_id)

        await self._mutate(user_id, ("shelf.delete", shelf_id, {}), idempotency_key, mutate, items)

    # ------------------------------------------------------------------ внутреннее
    def _title(self, title: str) -> str:
        try:
            return self._policy.check(
                title, max_len=self._limits.shelf_title_max, forbid_links=True
            )
        except UgcViolation as exc:
            raise TitleInvalidError(exc.code, exc.details) from None

    async def _hit(self, bucket: str, user_id: UUID) -> None:
        await self._limiter.hit(
            bucket, str(user_id), limit=self._limits.saves_per_min, window_s=RATE_WINDOW_S
        )

    async def _mutate(
        self,
        user_id: UUID,
        request: tuple[str, object, dict[str, Any]],
        idempotency_key: str,
        mutate: Mutation,
        items: ItemsLoader | None,
    ) -> Json:
        action, target, body = request
        fingerprint = request_fingerprint(action, target, body)
        async with self._uow() as uow:

            async def perform() -> Json:
                now = self._clock.now()
                version = await uow.library.lock_user(user_id, now)
                shelves = await uow.library.shelves(user_id)
                custom_ids = frozenset(s.id for s in shelves if s.kind is ShelfKind.CUSTOM)
                loaded = (
                    await items(uow, UserLibraryInput(user_id, custom_ids))
                    if items is not None
                    else []
                )
                lib = UserLibrary(
                    user_id,
                    version,
                    shelves,
                    loaded,
                    catalog=self._catalog,
                    limits=self._limits.library,
                )
                response = await mutate(lib, uow, now)
                await uow.library.save(lib, now)
                uow.track(lib)
                return response

            response, executed = await run_idempotent(
                uow.idempotency, user_id, idempotency_key, fingerprint, perform
            )
            await uow.commit()
        if not executed:
            log.info("library.idempotent_replay", action=action)
        return response
