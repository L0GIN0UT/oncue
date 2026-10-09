"""Генерация набора ассетов (задача RabbitMQ ``media.generate_asset_set``) и перегенерация."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import AppError
from gv_messaging.ports import TaskQueue
from media_pipeline_service.application.commands.catalog import schedule_generation
from media_pipeline_service.application.dto import AssetSetView
from media_pipeline_service.application.errors import GenerationError, GenerationRetryError
from media_pipeline_service.application.generators.base import GeneratorRegistry
from media_pipeline_service.application.ports import (
    AdminAction,
    ObjectStorage,
    SourceFetcher,
    StorageArea,
    TokenSource,
    UnitOfWorkFactory,
)
from media_pipeline_service.application.sources import SourceIngestor, delete_quietly
from media_pipeline_service.domain.asset_set import AssetLevel, AssetSet, AssetSetStatus
from media_pipeline_service.domain.errors import AssetSetNotFoundError
from media_pipeline_service.domain.links import asset_object_key
from media_pipeline_service.domain.sources import SourceMedia, SourceStatus

log = structlog.get_logger(__name__)


class TaskNotVisibleError(Exception):
    """Набор или его поколение ещё не видны (транзакция постановщика не закоммичена)."""


@dataclass(frozen=True, slots=True)
class _Started:
    aggregate: AssetSet
    source: SourceMedia | None


class GenerateAssetSet:
    """Сгенерировать объекты уровней текущего поколения и опубликовать результат.

    Идемпотентность: задача несёт ``generation``; устаревшая (после перегенерации или
    готовности) ничего не делает. Повтор той же задачи перезаписывает строки уровней
    новыми ключами; объекты прошлой попытки, не попавшие в БД, удаляются.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        *,
        registry: GeneratorRegistry,
        storage: ObjectStorage,
        fetcher: SourceFetcher,
        ingestor: SourceIngestor,
        tokens: TokenSource,
        max_attempts: int,
        max_source_bytes: int,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._registry = registry
        self._storage = storage
        self._fetcher = fetcher
        self._ingestor = ingestor
        self._tokens = tokens
        self._max_attempts = max_attempts
        self._max_source_bytes = max_source_bytes

    async def execute(self, asset_set_id: UUID, generation: int) -> None:
        """Выполнить задачу.

        Raises:
            TaskNotVisibleError: набор ещё не виден — задачу нужно повторить позже.
            GenerationRetryError: попытка не удалась, будет повтор через очередь.
        """
        started = await self._start(asset_set_id, generation)
        if started is None:
            return
        uploaded: list[str] = []
        try:
            levels = await self._produce(started, uploaded)
        except GenerationError as exc:
            await self._fail(
                asset_set_id, generation, exc.code, retryable=exc.retryable, uploaded=uploaded
            )
            return
        except Exception:
            # Неожиданное (S3, баг адаптера): повторяем, деталь — только в логе.
            log.exception("asset_set.generation_crashed", asset_set_id=str(asset_set_id))
            await self._fail(
                asset_set_id, generation, "internal.error", retryable=True, uploaded=uploaded
            )
            return
        await self._complete(asset_set_id, generation, levels, uploaded)

    async def _start(self, asset_set_id: UUID, generation: int) -> _Started | None:
        async with self._uow() as uow:
            aggregate = await uow.asset_sets.get(asset_set_id, for_update=True)
            if aggregate is None or generation > aggregate.generation:
                raise TaskNotVisibleError(f"набор {asset_set_id} поколения {generation} не виден")
            if not aggregate.is_current(generation):
                log.info(
                    "asset_set.task_stale", asset_set_id=str(asset_set_id), generation=generation
                )
                return None
            item = await uow.items.get(aggregate.media_item_id)
            if item is not None and item.is_blocked:
                log.info("asset_set.item_blocked", asset_set_id=str(asset_set_id))
                return None
            aggregate.start_attempt(generation, self._clock.now())
            await uow.asset_sets.save(aggregate)
            source = (
                await uow.sources.get(aggregate.source_media_id)
                if aggregate.source_media_id is not None
                else None
            )
            await uow.commit()
        return _Started(aggregate, source)

    async def _produce(self, started: _Started, uploaded: list[str]) -> list[AssetLevel]:
        aggregate = started.aggregate
        generator = self._registry.get(aggregate.clue_type)
        if generator is None:
            raise GenerationError("profile.unknown_clue_type", retryable=False)
        try:
            params = self._registry.params_for(aggregate.clue_type, aggregate.params)
        except (ValueError, AppError) as exc:
            raise GenerationError("profile.invalid", str(exc), retryable=False) from exc
        if started.source is None:
            raise GenerationError("source.missing", retryable=False)
        data = await self._load_source(started.source)
        generated = await generator.generate(data, params)
        levels: list[AssetLevel] = []
        for number, asset in enumerate(generated, start=1):
            key = asset_object_key(self._tokens.token_hex(), asset.ext)
            await self._storage.put(StorageArea.ASSETS, key, asset.data, asset.mime)
            uploaded.append(key)
            levels.append(
                AssetLevel(
                    level=number,
                    object_key=key,
                    mime=asset.mime,
                    size_bytes=len(asset.data),
                    duration_ms=asset.duration_ms,
                    width=asset.width,
                    height=asset.height,
                )
            )
        return levels

    async def _load_source(self, source: SourceMedia) -> bytes:
        if source.is_stored and source.object_key is not None:
            return await self._storage.get(
                StorageArea.SOURCES, source.object_key, max_bytes=self._max_source_bytes
            )
        if not source.uri:
            raise GenerationError("source.missing", retryable=False)
        fetched = await self._fetcher.fetch(source.uri)
        try:
            stored = await self._ingestor.ingest(source.kind, fetched.data)
        except AppError as exc:  # размер или сигнатура: повтор не поможет
            await self._mark_source_failed(source.id)
            raise GenerationError(exc.code, exc.message, retryable=False) from exc
        except GenerationError as exc:
            if not exc.retryable:  # файл не декодируется — исходник негоден
                await self._mark_source_failed(source.id)
            raise
        async with self._uow() as uow:
            fresh = await uow.sources.get(source.id)
            if fresh is not None:
                fresh.mark_stored(
                    object_key=stored.object_key,
                    sha256=stored.sha256,
                    size_bytes=stored.size_bytes,
                    mime=stored.mime,
                )
                await uow.sources.save(fresh)
            await uow.commit()
        return stored.data

    async def _mark_source_failed(self, source_id: UUID) -> None:
        async with self._uow() as uow:
            fresh = await uow.sources.get(source_id)
            if fresh is not None:
                fresh.status = SourceStatus.FAILED
                await uow.sources.save(fresh)
            await uow.commit()

    async def _fail(
        self,
        asset_set_id: UUID,
        generation: int,
        code: str,
        *,
        retryable: bool,
        uploaded: list[str],
    ) -> None:
        await delete_quietly(self._storage, StorageArea.ASSETS, uploaded)
        async with self._uow() as uow:
            aggregate = await uow.asset_sets.get(asset_set_id, for_update=True)
            if aggregate is None or not aggregate.is_current(generation):
                return
            final = aggregate.fail(
                generation,
                code,
                retryable=retryable,
                max_attempts=self._max_attempts,
                now=self._clock.now(),
            )
            await uow.asset_sets.save(aggregate)
            uow.track(aggregate)
            await uow.commit()
        log.warning(
            "asset_set.generation_failed",
            asset_set_id=str(asset_set_id),
            error_code=code,
            final=final,
            attempts=aggregate.attempts,
        )
        if not final:
            raise GenerationRetryError(code)

    async def _complete(
        self, asset_set_id: UUID, generation: int, levels: list[AssetLevel], uploaded: list[str]
    ) -> None:
        async with self._uow() as uow:
            aggregate = await uow.asset_sets.get(asset_set_id, for_update=True)
            if (
                aggregate is None
                or not aggregate.is_current(generation)
                or aggregate.status is not AssetSetStatus.PROCESSING
            ):
                # Пока генерировали, набор выключили или перегенерировали — результат не нужен.
                await delete_quietly(self._storage, StorageArea.ASSETS, uploaded)
                return
            aggregate.complete(generation, levels, self._clock.now())
            await uow.asset_sets.save(aggregate)
            uow.track(aggregate)
            await uow.commit()
        log.info(
            "asset_set.ready",
            asset_set_id=str(asset_set_id),
            clue_type=aggregate.clue_type,
            levels=len(levels),
        )


class RegenerateAssetSet:
    """Перегенерировать набор по запросу админа (новое поколение, новые ключи объектов)."""

    def __init__(
        self, uow: UnitOfWorkFactory, clock: Clock, queue: TaskQueue, storage: ObjectStorage
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._queue = queue
        self._storage = storage

    async def execute(self, asset_set_id: UUID, *, actor_id: UUID, reason: str) -> AssetSetView:
        """Перегенерировать.

        Raises:
            AssetSetNotFoundError: нет набора.
            AssetBlockedError: единица заблокирована.
        """
        async with self._uow() as uow:
            aggregate = await uow.asset_sets.get(asset_set_id, for_update=True)
            if aggregate is None:
                raise AssetSetNotFoundError("набор ассетов не найден")
            now = self._clock.now()
            old = aggregate.regenerate(now)
            await uow.asset_sets.save(aggregate)
            await uow.audit.record(
                AdminAction(
                    actor_id=actor_id,
                    action="asset_set.regenerate",
                    target_id=asset_set_id,
                    reason=reason,
                    at=now,
                    details={"generation": aggregate.generation},
                )
            )
            await schedule_generation(self._queue, aggregate)
            await uow.commit()
        await delete_quietly(self._storage, StorageArea.ASSETS, [lvl.object_key for lvl in old])
        log.info(
            "asset_set.regenerate_requested",
            asset_set_id=str(asset_set_id),
            actor_id=str(actor_id),
            generation=aggregate.generation,
        )
        return AssetSetView.of(aggregate)
