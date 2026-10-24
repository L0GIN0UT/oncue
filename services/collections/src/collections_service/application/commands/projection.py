"""Обработчики событий других сервисов и фоновые пересчёты (K7, K8, D9).

Работают в транзакции consumer (inbox + изменения атомарно). Порядок защищает ``version``
payload: событие не новее применённого отбрасывается (повтор, ретрай, перестановка).

* ``catalog.MediaItem*`` → ``catalog_items_proj``; ``Blocked`` убирает единицу из составов
  порциями: первая — сразу, остальное — задача ``collections.blocked.sweep``;
* ``game.GameFinished`` с ``collection_id`` → «пройден», ``plays_count``, сложность (только
  пользователи, не автор, первое завершение на версию);
* ``identity.UserRegistered|ProfileBasicsChanged`` → ник; ``UserDeleted`` → решение
  владельца: черновики удаляются, опубликованные обезличиваются.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

import structlog
from prometheus_client import Counter

from collections_service.application.ports import (
    AdminAction,
    CatalogItemSnapshot,
    CollectionsUnitOfWork,
    UnitOfWorkFactory,
)
from collections_service.domain.difficulty import DifficultyModel
from collections_service.domain.model import Status
from gv_core.clock import Clock

log = structlog.get_logger(__name__)

BLOCKED = "blocked"
ITEMS_REMOVED = Counter(
    "collections_blocked_items_removed_total",
    "Заблокированные единицы, убранные из составов сборников",
    ["path"],
)


def is_newer(version: int, current: int | None) -> bool:
    """Событие новее применённого."""
    return current is None or version > current


class CatalogEvents:
    """Проекция каталога и удаление заблокированных единиц из составов.

    Args:
        clock: Часы.
        batch: Сборников за один проход (consumer и задача sweep).
    """

    def __init__(self, clock: Clock, batch: int) -> None:
        self._clock = clock
        self._batch = batch

    async def snapshot(self, uow: CollectionsUnitOfWork, item: CatalogItemSnapshot) -> bool:
        """Полный снимок единицы; ``False`` — событие устарело."""
        if not is_newer(item.version, await uow.projection.item_version(item.media_item_id)):
            return False
        await uow.projection.upsert_item(item)
        if item.status == BLOCKED:
            await self.remove_from_collections(uow, item.media_item_id, path="consumer")
        return True

    async def status(
        self, uow: CollectionsUnitOfWork, media_item_id: UUID, status: str, version: int
    ) -> bool:
        """Блокировка/разблокировка; ``False`` — устарело или единицы ещё нет."""
        current = await uow.projection.item_version(media_item_id)
        if current is None:
            log.warning("collections.catalog_status_without_item", media_item_id=str(media_item_id))
            return False
        if not is_newer(version, current):
            return False
        await uow.projection.set_item_status(media_item_id, status, version)
        if status == BLOCKED:
            await self.remove_from_collections(uow, media_item_id, path="consumer")
        return True

    async def remove_from_collections(
        self, uow: CollectionsUnitOfWork, media_item_id: UUID, *, path: str
    ) -> int:
        """Убрать единицу из первых ``batch`` составов (по новой версии на сборник)."""
        removed = 0
        for collection_id in await uow.collections.ids_with_item(media_item_id, self._batch):
            collection = await uow.collections.get(collection_id, for_update=True)
            if collection is not None and collection.remove_item(media_item_id, self._clock.now()):
                await uow.collections.save(collection)
                uow.track(collection)
                removed += 1
        if removed:
            ITEMS_REMOVED.labels(path).inc(removed)
            log.info("collections.blocked_item_removed", collections=removed, path=path)
        return removed


class SweepBlockedItems:
    """Задача ``collections.blocked.sweep``: остаток составов с заблокированными единицами."""

    def __init__(self, uow: UnitOfWorkFactory, events: CatalogEvents, batch: int) -> None:
        self._uow = uow
        self._events = events
        self._batch = batch

    async def execute(self) -> int:
        """Один проход (порция ``batch`` пар); число изменённых сборников."""
        async with self._uow() as uow:
            pairs = await uow.collections.blocked_pairs(self._batch)
            removed = 0
            for media_item_id in dict.fromkeys(item for _, item in pairs):
                removed += await self._events.remove_from_collections(
                    uow, media_item_id, path="sweep"
                )
            await uow.commit()
        return removed


@dataclass(frozen=True, slots=True)
class GameFinishedData:
    """Нужное collections из ``game.GameFinished``."""

    session_id: UUID
    user_id: UUID | None
    is_guest: bool
    collection_id: UUID | None
    collection_version: int | None
    score: int
    max_score: int
    finished_at: datetime


class Completions:
    """«Пройден» и сложность (D9).

    Args:
        model: Формула сложности.
    """

    def __init__(self, model: DifficultyModel) -> None:
        self._model = model

    async def apply(self, uow: CollectionsUnitOfWork, data: GameFinishedData) -> bool:
        """Учесть завершение; ``False`` — не по сборнику, гость, автор или повтор версии."""
        if data.collection_id is None or data.is_guest or data.user_id is None:
            return False
        row = await uow.reads.row(data.collection_id)
        if row is None or row.content_version == 0 or row.author_id == data.user_id:
            return False
        version = data.collection_version or row.content_version
        ratio = min(1.0, max(0.0, data.score / data.max_score)) if data.max_score > 0 else None
        first = await uow.stats.add_completion(
            data.user_id,
            data.collection_id,
            version,
            ratio=ratio,
            session_id=data.session_id,
            at=data.finished_at,
        )
        if not first:
            return False
        _, ratio_sum, ratio_count = await uow.stats.add_play(data.collection_id, ratio)
        await uow.stats.set_difficulty(
            data.collection_id, self._model.score(ratio_sum, ratio_count)
        )
        await uow.stats.bump_daily(data.collection_id, data.finished_at.date(), likes=0, plays=1)
        return True


class ModerationEvents:
    """Решения модерации social: сборник, признанный нарушением, скрывается.

    Args:
        clock: Часы.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def content_removed(
        self, uow: CollectionsUnitOfWork, collection_id: UUID, *, case_id: UUID, moderator_id: UUID
    ) -> bool:
        """``social.ContentModerated`` (content_removed, collection): скрыть сборник.

        Тот же механизм, что у ручного скрытия админом (``hidden`` + ``CollectionUpdated``).
        Идемпотентно: сборника нет, он удалён, не опубликован или уже скрыт - без изменений.

        Returns:
            ``True``, если сборник скрыт этим событием.
        """
        collection = await uow.collections.get(collection_id, for_update=True)
        if collection is None or collection.state.status is not Status.PUBLISHED:
            log.info("collections.moderation_skipped", collection_id=str(collection_id))
            return False
        now = self._clock.now()
        collection.hide(reason=f"social.case:{case_id}", actor_id=moderator_id, now=now)
        await uow.collections.save(collection)
        uow.track(collection)
        await uow.audit.record(
            AdminAction(
                actor_id=moderator_id,
                action="collection.hide",
                target=str(collection_id),
                reason=f"решение модерации social, кейс {case_id}",
                at=now,
                details={"case_id": str(case_id), "source": "social"},
            )
        )
        return True


class UserEvents:
    """Ники авторов и удаление аккаунта (решение владельца, п. 5).

    Args:
        clock: Часы.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def profile(
        self, uow: CollectionsUnitOfWork, user_id: UUID, nickname: str, version: int
    ) -> bool:
        """``UserRegistered|ProfileBasicsChanged``; ``False`` — устарело."""
        if not is_newer(version, await uow.projection.user_version(user_id)):
            return False
        await uow.projection.upsert_user(user_id, nickname, version)
        return True

    async def deleted(self, uow: CollectionsUnitOfWork, user_id: UUID, version: int) -> int:
        """``UserDeleted``: черновики удаляются, опубликованные — «автор удалён»
        (``CollectionUpdated``); лайки, избранное, завершения и ограничение — удаляются.

        Returns:
            Число затронутых сборников автора.
        """
        if is_newer(version, await uow.projection.user_version(user_id)):
            await uow.projection.upsert_user(user_id, None, version)
        now = self._clock.now()
        touched = 0
        for collection_id in await uow.collections.ids_by_author(user_id):
            collection = await uow.collections.get(collection_id, for_update=True)
            if collection is None:
                continue
            touched += 1
            if collection.forget_author(now):
                await uow.collections.purge(collection_id)
            else:
                await uow.collections.save(collection)
                uow.track(collection)
        await uow.reactions.forget_user(user_id)
        await uow.stats.forget_user(user_id)
        await uow.restrictions.delete(user_id)
        log.info("collections.user_forgotten", collections=touched)
        return touched


class PopularRecompute:
    """Задача ``collections.popular.recompute`` (D10): окно ``window_days``, порциями.

    Args:
        uow: Фабрика транзакций (своя транзакция на порцию).
        clock: Часы.
        window_days: Окно популярности.
        like_weight: Вес лайка.
        play_weight: Вес завершения.
        batch: Сборников за порцию.
        retention_days: Сколько хранить дневную статистику.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        *,
        window_days: int,
        like_weight: float,
        play_weight: float,
        batch: int,
        retention_days: int,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._window = window_days
        self._like_weight = like_weight
        self._play_weight = play_weight
        self._batch = batch
        self._retention = max(retention_days, window_days)

    async def execute(self) -> int:
        """Пересчитать всё; число порций."""
        today: date = self._clock.now().date()
        since = today - timedelta(days=self._window - 1)
        after: UUID | None = None
        batches = 0
        while True:
            async with self._uow() as uow:
                after = await uow.stats.recompute_popular(
                    since,
                    like_weight=self._like_weight,
                    play_weight=self._play_weight,
                    after=after,
                    limit=self._batch,
                )
                await uow.commit()
            if after is None:
                break
            batches += 1
        async with self._uow() as uow:
            await uow.stats.purge_daily(today - timedelta(days=self._retention))
            await uow.commit()
        return batches
