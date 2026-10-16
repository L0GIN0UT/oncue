"""Свои загадки сборников (ADR 0029): события ``collections.collection.v1`` → текстовые
исходники и наборы со ``scope`` сборника.

Механика та же, что у текстов загадок catalog (ADR 0024): эмодзи автора становятся
текстовым исходником единицы, id которого — хэш содержимого и владельца. Правка эмодзи даёт
новый исходник, и ``PlanAssetSets`` переводит набор в новое поколение; повтор того же
снимка ничего не меняет. Эмодзи убрали, сборник скрыт или удалён — наборы и исходники
владельца удаляются, объекты уровней — после коммита (``delete_quietly``, как у прошлых
поколений).

Порядок блокировок как у воркера и consumer catalog: строки единиц (по возрастанию id) →
наборы. Задачи генерации ставятся до коммита (см. ``commands/catalog.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_text.emoji import is_strict_emoji
from media_pipeline_service.application.commands.catalog import PlanAssetSets
from media_pipeline_service.application.ports import (
    MediaUnitOfWork,
    ObjectStorage,
    StorageArea,
    UnitOfWorkFactory,
)
from media_pipeline_service.application.sources import delete_quietly
from media_pipeline_service.domain.collections import CollectionState, CollectionStatus
from media_pipeline_service.domain.errors import CollectionContentInvalidError
from media_pipeline_service.domain.sources import AssetScope, SourceMedia
from media_pipeline_service.domain.texts import text_content, text_source_identity

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CollectionItemCustom:
    """Единица сборника и свои загадки автора к ней.

    Attributes:
        media_item_id: Единица каталога.
        emoji: Эмодзи автора; ``None`` — своих загадок нет.
    """

    media_item_id: UUID
    emoji: str | None = None


@dataclass(frozen=True, slots=True)
class CollectionSnapshot:
    """Снимок ``CollectionPublished``/``CollectionUpdated`` в объёме, нужном сервису."""

    collection_id: UUID
    version: int
    domain: str
    status: CollectionStatus
    items: tuple[CollectionItemCustom, ...]


@dataclass(frozen=True, slots=True)
class CollectionSyncResult:
    """Итог обработки события: ``applied = False`` — событие устарело."""

    applied: bool
    scheduled: tuple[UUID, ...] = ()
    deleted_sets: int = 0


class SyncCollectionAssets:
    """Привести исходники и наборы со ``scope`` сборника к снимку события.

    Args:
        emoji_max_graphemes: Лимит графем эмодзи (``GV_MEDIA__COLLECTION_EMOJI_MAX_GRAPHEMES``,
            как у collections: ``GV_COLLECTIONS__LIMITS__EMOJI_MAX_GRAPHEMES``).
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        plan: PlanAssetSets,
        storage: ObjectStorage,
        *,
        emoji_max_graphemes: int,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._plan = plan
        self._storage = storage
        self._emoji_max_graphemes = emoji_max_graphemes

    async def apply_snapshot(self, snapshot: CollectionSnapshot) -> CollectionSyncResult:
        """``CollectionPublished``/``CollectionUpdated``.

        Raises:
            CollectionContentInvalidError: эмодзи вне строгого набора RGI или длиннее лимита,
                единица повторяется (контракт нарушен — повтор события не поможет).
        """
        desired = self._desired(snapshot)
        state = CollectionState(
            snapshot.collection_id,
            version=snapshot.version,
            status=snapshot.status,
            updated_at=self._clock.now(),
        )
        return await self._apply(state, desired, domain=snapshot.domain)

    async def apply_deleted(self, collection_id: UUID, version: int) -> CollectionSyncResult:
        """``CollectionDeleted``: наборы и исходники сборника удаляются, проекция — надгробие."""
        state = CollectionState(
            collection_id,
            version=version,
            status=CollectionStatus.DELETED,
            updated_at=self._clock.now(),
        )
        return await self._apply(state, {}, domain=None)

    def _desired(self, snapshot: CollectionSnapshot) -> dict[UUID, str]:
        """Эмодзи по единицам (защита в глубину: collections проверяет то же при публикации).

        Скрытому сборнику свои загадки не нужны: содержимое не проверяется, чтобы скрытие
        модератором удалило наборы даже у сборника с испорченными данными.
        """
        if snapshot.status is not CollectionStatus.PUBLISHED:
            return {}
        seen: set[UUID] = set()
        desired: dict[UUID, str] = {}
        for position, item in enumerate(snapshot.items):
            if item.media_item_id in seen:
                raise CollectionContentInvalidError(
                    "единица повторяется в сборнике",
                    details={"position": position, "reason": "item_duplicate"},
                )
            seen.add(item.media_item_id)
            if item.emoji is None:
                continue
            if not is_strict_emoji(item.emoji, max_graphemes=self._emoji_max_graphemes):
                # Сами эмодзи в лог и details не пишем: это UGC.
                raise CollectionContentInvalidError(
                    "эмодзи сборника вне строгого набора",
                    details={"position": position, "reason": "emoji_invalid"},
                )
            desired[item.media_item_id] = item.emoji
        return desired

    async def _apply(
        self, state: CollectionState, desired: dict[UUID, str], *, domain: str | None
    ) -> CollectionSyncResult:
        scope = AssetScope.collection(state.id)
        stale_keys: list[str] = []
        async with self._uow() as uow:
            if not await uow.collections.advance(state):
                log.info(
                    "collection_event.stale", collection_id=str(state.id), version=state.version
                )
                return CollectionSyncResult(applied=False)
            scheduled, deleted = await self._reconcile(
                uow, scope, desired, domain=domain, stale_keys=stale_keys
            )
            await uow.commit()
        await delete_quietly(self._storage, StorageArea.ASSETS, stale_keys)
        log.info(
            "collection_assets.synced",
            collection_id=str(state.id),
            version=state.version,
            status=state.status.value,
            items=len(desired),
            scheduled=len(scheduled),
            deleted_sets=deleted,
        )
        return CollectionSyncResult(applied=True, scheduled=scheduled, deleted_sets=deleted)

    async def _reconcile(
        self,
        uow: MediaUnitOfWork,
        scope: AssetScope,
        desired: dict[UUID, str],
        *,
        domain: str | None,
        stale_keys: list[str],
    ) -> tuple[tuple[UUID, ...], int]:
        sources = await uow.sources.list_for_scope(scope)
        current = {s.media_item_id for s in sources}
        current |= {a.media_item_id for a in await uow.asset_sets.list_for_scope(scope)}
        # Сначала строки единиц (по возрастанию id), затем наборы — как воркер и catalog.
        states = await uow.items.get_many(sorted(current | desired.keys()), for_update=True)
        sets = await uow.asset_sets.list_for_scope(scope, for_update=True)

        # Эмодзи убрали (или сборник скрыт/удалён): наборы и исходники владельца удаляются.
        gone_sets = [a for a in sets if a.media_item_id not in desired]
        for aggregate in gone_sets:
            stale_keys.extend(level.object_key for level in aggregate.levels)
        await uow.asset_sets.delete([a.id for a in gone_sets])
        await uow.sources.delete([s.id for s in sources if s.media_item_id not in desired])

        scheduled: list[UUID] = []
        for media_item_id, emoji in desired.items():
            item = states.get(media_item_id)
            if item is None or item.domain is None:
                # catalog-событие единицы ещё не дошло (FK исходника): свои загадки появятся
                # со следующей версией сборника.
                log.warning(
                    "collection_assets.item_unknown",
                    collection_id=str(scope.id),
                    media_item_id=str(media_item_id),
                )
                continue
            if item.domain != domain:
                log.warning(
                    "collection_assets.domain_mismatch",
                    collection_id=str(scope.id),
                    media_item_id=str(media_item_id),
                )
                continue
            own = [s for s in sources if s.media_item_id == media_item_id]
            source_id = await self._put_source(uow, scope, media_item_id, emoji, own)
            planned = await self._plan.plan_in(uow, media_item_id, scope=scope)
            stale_keys.extend(planned.stale_keys)
            scheduled.extend(planned.asset_set_ids)
            # Набор, который новый исходник не принял (эмодзи меньше, чем открывает профиль),
            # показывал бы прежние эмодзи автора — удаляем.
            orphaned = [
                a
                for a in sets
                if a.media_item_id == media_item_id
                and a.id not in planned.asset_set_ids
                and source_id not in a.source_media_ids
            ]
            for aggregate in orphaned:
                stale_keys.extend(level.object_key for level in aggregate.levels)
            await uow.asset_sets.delete([a.id for a in orphaned])
            gone_sets.extend(orphaned)
        return tuple(scheduled), len(gone_sets)

    async def _put_source(
        self,
        uow: MediaUnitOfWork,
        scope: AssetScope,
        media_item_id: UUID,
        emoji: str,
        own: Sequence[SourceMedia],
    ) -> UUID:
        """Текстовый исходник эмодзи: тот же — ничего; другой — замена (новое поколение).

        Returns:
            id текущего исходника.
        """
        content = text_content(
            clue_texts={"emoji": emoji}, year=None, genres=(), countries=(), directors=(), cast=()
        )
        source_id, digest, size = text_source_identity(media_item_id, content, scope=scope)
        if any(s.id == source_id for s in own):
            return source_id
        await uow.sources.delete([s.id for s in own])
        await uow.sources.add(
            SourceMedia.text(
                source_id,
                media_item_id=media_item_id,
                content=content,
                sha256=digest,
                size_bytes=size,
                created_at=self._clock.now(),
                scope=scope,
            )
        )
        return source_id
