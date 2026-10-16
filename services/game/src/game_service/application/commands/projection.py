"""Обработчики событий других сервисов: проекции каталога, ассетов и сборников, удаление
пользователя.

Работают в транзакции consumer (inbox + изменения атомарно). Порядок защищает ``version``
payload: событие не новее применённого отбрасывается (повтор, ретрай, перестановка).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import structlog

from game_service.application.ports import GameUnitOfWork
from game_service.domain.projection import (
    AssetStatus,
    CatalogItem,
    ClueAssets,
    CollectionItem,
    CollectionProj,
    CollectionStatus,
    ItemStatus,
    is_newer,
    scoped_keep,
)

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AssetsReadyData:
    """Payload ``media_pipeline.AssetsReady``."""

    asset_set_id: UUID
    media_item_id: UUID
    clue_type: str
    params_hash: str
    levels_count: int
    version: int
    scope_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class CollectionSnapshot:
    """Payload ``collections.CollectionPublished|Updated``: снимок сборника и состав."""

    proj: CollectionProj
    items: tuple[CollectionItem, ...]


class ApplyCatalogEvent:
    """``catalog.MediaItemCreated|Updated|Blocked|Unblocked`` → проекция ``catalog_items``."""

    async def snapshot(self, uow: GameUnitOfWork, item: CatalogItem) -> bool:
        """Полный снимок единицы; ``False`` — событие устарело."""
        current = await uow.projection.get_item(item.media_item_id, for_update=True)
        if not is_newer(item.version, current.version if current else None):
            log.info("game.catalog_event_stale", media_item_id=str(item.media_item_id))
            return False
        await uow.projection.upsert_item(item)
        return True

    async def status(
        self, uow: GameUnitOfWork, media_item_id: UUID, status: ItemStatus, version: int
    ) -> bool:
        """Блокировка или разблокировка; ``False`` — устарело или единицы ещё нет."""
        current = await uow.projection.get_item(media_item_id, for_update=True)
        if current is None:
            # Снимок единицы придёт событием Created (порядок по ключу партиции сохраняется).
            log.warning("game.catalog_status_without_item", media_item_id=str(media_item_id))
            return False
        if not is_newer(version, current.version):
            return False
        await uow.projection.set_item_status(media_item_id, str(status), version)
        return True


class ApplyAssetsEvent:
    """``media_pipeline.AssetsReady|AssetsFailed`` → проекция ``clue_assets`` (ADR 0015:
    версия монотонна по ``media_item_id``)."""

    async def ready(self, uow: GameUnitOfWork, data: AssetsReadyData) -> bool:
        """Набор готов; ``False`` — событие устарело или сборник набора скрыт/удалён."""
        current = await uow.projection.item_asset_version(data.media_item_id, lock=True)
        if not is_newer(data.version, current):
            return False
        if data.scope_id is not None:
            owner = await uow.collections.get(data.scope_id)
            if owner is not None and not owner.is_playable:
                # Набор собран до скрытия или удаления сборника (топики разные — порядок
                # между ними не гарантирован); media-pipeline его уже удалил.
                log.info("game.scoped_assets_skipped", collection_id=str(data.scope_id))
                return False
        await uow.projection.upsert_assets(
            ClueAssets(
                media_item_id=data.media_item_id,
                clue_type=data.clue_type,
                params_hash=data.params_hash,
                asset_set_id=data.asset_set_id,
                levels_count=data.levels_count,
                status=AssetStatus.READY,
                version=data.version,
                scope_id=data.scope_id,
            )
        )
        # Запрос ассетов под этот хэш выполнен: профиль media-pipeline отдаёт наборы (ADR 0025).
        await uow.asset_requests.mark_sent(data.clue_type, data.params_hash)
        return True

    async def failed(
        self, uow: GameUnitOfWork, media_item_id: UUID, asset_set_id: UUID, version: int
    ) -> bool:
        """Генерация набора не удалась; ``False`` — устарело или набора нет в проекции."""
        current = await uow.projection.item_asset_version(media_item_id, lock=True)
        if not is_newer(version, current):
            return False
        return await uow.projection.fail_assets(asset_set_id, version)


class ApplyCollectionEvent:
    """``collections.CollectionPublished|Updated|Deleted`` → ``collection_proj`` и состав
    (ADR 0028). Порядок защищает общая монотонная ``version`` сборника.

    Событий об удалении наборов сборника media-pipeline не шлёт (ADR 0029): свои
    scoped-ассеты game убирает сам — у единиц без своей загадки в снимке, у скрытого и
    удалённого сборника. Начатые сессии доигрываются: план уже разыгран, раунд без набора
    берёт каталожный или аннулируется (``void``).
    """

    async def snapshot(self, uow: GameUnitOfWork, data: CollectionSnapshot) -> bool:
        """Снимок сборника; ``False`` — событие устарело."""
        cid = data.proj.collection_id
        current = await uow.collections.get(cid, for_update=True)
        if not is_newer(data.proj.version, current.version if current else None):
            log.info("game.collection_event_stale", collection_id=str(cid))
            return False
        await uow.collections.save(data.proj, data.items)
        removed = await uow.projection.delete_scoped_assets(
            cid, scoped_keep(data.proj.status, data.items)
        )
        log.info(
            "game.collection_projected",
            collection_id=str(cid),
            status=str(data.proj.status),
            items=len(data.items),
            scoped_removed=removed,
        )
        return True

    async def deleted(
        self, uow: GameUnitOfWork, collection_id: UUID, version: int, at: datetime
    ) -> bool:
        """Сборник удалён: надгробие без единиц и без своих наборов; ``False`` — устарело."""
        current = await uow.collections.get(collection_id, for_update=True)
        if not is_newer(version, current.version if current else None):
            log.info("game.collection_event_stale", collection_id=str(collection_id))
            return False
        tombstone = CollectionProj(
            collection_id=collection_id,
            version=version,
            content_version=current.content_version if current else 0,
            domain=current.domain if current else None,
            status=CollectionStatus.DELETED,
            visibility=current.visibility if current else None,
            author_id=None,
            updated_at=at,
        )
        await uow.collections.save(tombstone, ())
        removed = await uow.projection.delete_scoped_assets(collection_id, frozenset())
        log.info(
            "game.collection_deleted", collection_id=str(collection_id), scoped_removed=removed
        )
        return True


class ForgetUser:
    """``identity.UserDeleted``: ``player_id`` обнуляется, результаты остаются в статистике."""

    async def execute(self, uow: GameUnitOfWork, user_id: UUID) -> int:
        """Число обезличенных сессий."""
        count = await uow.sessions.forget_player(user_id)
        log.info("game.user_forgotten", sessions=count)
        return count
