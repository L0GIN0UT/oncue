"""Реакция на события catalog: проекция единицы, исходники, планирование и выключение наборов.

Задачи генерации ставятся в очередь ДО коммита транзакции: в consumer коммит делает
``EventProcessor`` уже после обработчика. Если коммит не случится, воркер не найдёт набор
(или найдёт старое поколение) и вернёт задачу на повтор, а затем в DLQ — потерь нет, лишняя
работа исключена проверкой поколения. Обратный порядок (сначала коммит) мог бы оставить
набор в ``pending`` навсегда при сбое RabbitMQ.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.ids import uuid7
from gv_messaging.ports import TaskQueue
from media_pipeline_service.application.dto import (
    GENERATE_TASK,
    CatalogSource,
    MediaItemSnapshot,
)
from media_pipeline_service.application.ports import (
    MediaUnitOfWork,
    ObjectStorage,
    StorageArea,
    UnitOfWorkFactory,
)
from media_pipeline_service.application.sources import delete_quietly
from media_pipeline_service.domain.asset_set import AssetSet, AssetSetStatus
from media_pipeline_service.domain.media_items import ItemStatus, MediaItemState
from media_pipeline_service.domain.profiles import AssetProfileCatalog
from media_pipeline_service.domain.sources import SourceMedia, SourceStatus, best_source

log = structlog.get_logger(__name__)


async def schedule_generation(queue: TaskQueue, aggregate: AssetSet) -> None:
    """Поставить задачу генерации текущего поколения набора."""
    await queue.enqueue(
        GENERATE_TASK,
        {"asset_set_id": str(aggregate.id), "generation": aggregate.generation},
        task_id=f"{aggregate.id}:{aggregate.generation}",
    )


class RegisterSourceFromCatalog:
    """Зарегистрировать исходники из снимка единицы (скачиваются позже, в задаче генерации)."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, media_item_id: UUID, sources: Sequence[CatalogSource]) -> None:
        """Добавить новые исходники; смена адреса у известного — повторная загрузка."""
        async with self._uow() as uow:
            for item in sources:
                existing = await uow.sources.get(item.source_media_ref)
                if existing is None:
                    await uow.sources.add(
                        SourceMedia(
                            item.source_media_ref,
                            media_item_id=media_item_id,
                            kind=item.kind,
                            origin=item.origin,
                            uri=item.uri,
                            created_at=self._clock.now(),
                            position=item.position,
                            source_url=item.source_url,
                            license_note=item.license_note,
                        )
                    )
                    continue
                if existing.media_item_id != media_item_id:
                    continue
                changed = False
                if existing.uri != item.uri:
                    existing.uri = item.uri
                    existing.object_key = None
                    existing.status = SourceStatus.REGISTERED
                    changed = True
                meta = (item.position, item.source_url, item.license_note)
                if (existing.position, existing.source_url, existing.license_note) != meta:
                    existing.move_to(item.position)
                    existing.source_url, existing.license_note = item.source_url, item.license_note
                    changed = True
                if changed:
                    await uow.sources.save(existing)
            await uow.commit()


@dataclass(frozen=True, slots=True)
class PlannedSets:
    """Результат планирования: поставленные наборы и объекты прошлых поколений к удалению."""

    asset_set_ids: tuple[UUID, ...]
    stale_keys: tuple[str, ...]


class PlanAssetSets:
    """Создать недостающие наборы по профилям домена единицы и перегенерировать наборы,
    для которых появился лучший исходник."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        profiles: AssetProfileCatalog,
        queue: TaskQueue,
        storage: ObjectStorage,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._profiles = profiles
        self._queue = queue
        self._storage = storage

    async def execute(self, media_item_id: UUID) -> list[UUID]:
        """Запланировать генерацию в своей транзакции. Возвращает id поставленных наборов."""
        async with self._uow() as uow:
            planned = await self.plan_in(uow, media_item_id)
            await uow.commit()
        await delete_quietly(self._storage, StorageArea.ASSETS, planned.stale_keys)
        return list(planned.asset_set_ids)

    async def plan_in(self, uow: MediaUnitOfWork, media_item_id: UUID) -> PlannedSets:
        """Запланировать внутри открытого UoW вызывающего (коммит — его забота).

        Задачи ставятся до коммита (см. докстринг модуля); объекты прошлых поколений
        вызывающий удаляет после коммита (``PlannedSets.stale_keys``).
        """
        stale_keys: list[str] = []
        scheduled: list[AssetSet] = []
        state = await uow.items.get(media_item_id)
        if state is None or state.is_blocked or state.domain is None:
            return PlannedSets((), ())
        sources = await uow.sources.list_for_item(media_item_id)
        existing = await uow.asset_sets.list_for_item(media_item_id, for_update=True)
        by_key = {(s.clue_type, s.profile_key, s.params_hash): s for s in existing}
        now = self._clock.now()
        for profile in self._profiles.for_domain(state.domain):
            source = best_source(sources, profile.source_kind)
            if source is None:
                continue
            current = by_key.get((profile.clue_type, profile.profile_key, profile.params_hash))
            if current is None:
                created = AssetSet.plan(
                    uuid7(),
                    media_item_id=media_item_id,
                    profile=profile,
                    source_media_ids=(source.id,),
                    now=now,
                )
                await uow.asset_sets.add(created)
                scheduled.append(created)
            elif (
                current.source_media_id != source.id
                and current.status is not AssetSetStatus.DISABLED
            ):
                old = current.regenerate(now, source_media_ids=(source.id,))
                stale_keys.extend(level.object_key for level in old)
                await uow.asset_sets.save(current)
                scheduled.append(current)
        for aggregate in scheduled:
            await schedule_generation(self._queue, aggregate)
        if scheduled:
            log.info("asset_sets.planned", media_item_id=str(media_item_id), count=len(scheduled))
        return PlannedSets(tuple(a.id for a in scheduled), tuple(stale_keys))


class DisableItemAssets:
    """Выключить все наборы единицы и удалить их объекты (единица заблокирована)."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, storage: ObjectStorage) -> None:
        self._uow = uow
        self._clock = clock
        self._storage = storage

    async def execute(self, media_item_id: UUID) -> int:
        """Выключить. Возвращает число выключенных наборов."""
        keys: list[str] = []
        disabled = 0
        async with self._uow() as uow:
            for aggregate in await uow.asset_sets.list_for_item(media_item_id, for_update=True):
                if aggregate.status is AssetSetStatus.DISABLED:
                    continue
                keys.extend(level.object_key for level in aggregate.disable(self._clock.now()))
                await uow.asset_sets.save(aggregate)
                disabled += 1
            await uow.commit()
        # Удаляем объекты: ссылки, выданные до блокировки, перестанут работать сразу,
        # а не через TTL (заблокированное — часто по жалобе правообладателя).
        await delete_quietly(self._storage, StorageArea.ASSETS, keys)
        return disabled


class RestoreItemAssets:
    """Вернуть наборы после разблокировки: новое поколение генерации."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, queue: TaskQueue) -> None:
        self._uow = uow
        self._clock = clock
        self._queue = queue

    async def execute(self, media_item_id: UUID) -> list[UUID]:
        """Восстановить выключенные наборы и поставить их генерацию."""
        restored: list[AssetSet] = []
        async with self._uow() as uow:
            for aggregate in await uow.asset_sets.list_for_item(media_item_id, for_update=True):
                if aggregate.status is not AssetSetStatus.DISABLED:
                    continue
                aggregate.restore(self._clock.now())
                await uow.asset_sets.save(aggregate)
                restored.append(aggregate)
            for aggregate in restored:
                await schedule_generation(self._queue, aggregate)
            await uow.commit()
        return [a.id for a in restored]


async def _apply_projection(
    uow: MediaUnitOfWork,
    *,
    media_item_id: UUID,
    version: int,
    status: ItemStatus,
    clock: Clock,
    snapshot: MediaItemSnapshot | None,
) -> bool:
    state = await uow.items.get(media_item_id, for_update=True)
    now = clock.now()
    if state is None:
        state = MediaItemState(
            media_item_id,
            domain=snapshot.domain if snapshot else None,
            kind=snapshot.kind if snapshot else None,
            status=status,
            version=version,
            updated_at=now,
        )
    elif snapshot is not None:
        if not state.apply_snapshot(
            version=version, domain=snapshot.domain, kind=snapshot.kind, status=status, now=now
        ):
            return False
    elif not state.apply_status(version=version, status=status, now=now):
        return False
    await uow.items.upsert(state)
    return True


class SyncMediaItem:
    """Обработать снимок единицы и статус-события: проекция → исходники → наборы.

    Устаревшее по ``version`` событие ничего не меняет.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        *,
        register_sources: RegisterSourceFromCatalog,
        plan: PlanAssetSets,
        disable: DisableItemAssets,
        restore: RestoreItemAssets,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._register = register_sources
        self._plan = plan
        self._disable = disable
        self._restore = restore

    async def apply_snapshot(self, snapshot: MediaItemSnapshot) -> bool:
        """``MediaItemCreated``/``MediaItemUpdated``. ``False`` — событие устарело."""
        async with self._uow() as uow:
            applied = await _apply_projection(
                uow,
                media_item_id=snapshot.media_item_id,
                version=snapshot.version,
                status=snapshot.status,
                clock=self._clock,
                snapshot=snapshot,
            )
            if not applied:
                log.info(
                    "catalog_event.stale",
                    media_item_id=str(snapshot.media_item_id),
                    version=snapshot.version,
                )
                return False
            await uow.commit()
        await self._register.execute(snapshot.media_item_id, snapshot.sources)
        await self._after_status(snapshot.media_item_id, snapshot.status)
        return True

    async def apply_status(self, media_item_id: UUID, version: int, status: ItemStatus) -> bool:
        """``MediaItemBlocked``/``MediaItemUnblocked``. ``False`` — событие устарело."""
        async with self._uow() as uow:
            applied = await _apply_projection(
                uow,
                media_item_id=media_item_id,
                version=version,
                status=status,
                clock=self._clock,
                snapshot=None,
            )
            if not applied:
                log.info("catalog_event.stale", media_item_id=str(media_item_id), version=version)
                return False
            await uow.commit()
        await self._after_status(media_item_id, status)
        return True

    async def _after_status(self, media_item_id: UUID, status: ItemStatus) -> None:
        if status is ItemStatus.BLOCKED:
            await self._disable.execute(media_item_id)
            return
        await self._restore.execute(media_item_id)
        await self._plan.execute(media_item_id)
