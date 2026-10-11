"""Обработчики событий других сервисов: проекции каталога и ассетов, удаление пользователя.

Работают в транзакции consumer (inbox + изменения атомарно). Порядок защищает ``version``
payload: событие не новее применённого отбрасывается (повтор, ретрай, перестановка).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog

from game_service.application.ports import GameUnitOfWork
from game_service.domain.projection import (
    AssetStatus,
    CatalogItem,
    ClueAssets,
    ItemStatus,
    is_newer,
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
        """Набор готов; ``False`` — событие устарело."""
        current = await uow.projection.item_asset_version(data.media_item_id, lock=True)
        if not is_newer(data.version, current):
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
            )
        )
        return True

    async def failed(
        self, uow: GameUnitOfWork, media_item_id: UUID, asset_set_id: UUID, version: int
    ) -> bool:
        """Генерация набора не удалась; ``False`` — устарело или набора нет в проекции."""
        current = await uow.projection.item_asset_version(media_item_id, lock=True)
        if not is_newer(version, current):
            return False
        return await uow.projection.fail_assets(asset_set_id, version)


class ForgetUser:
    """``identity.UserDeleted``: ``player_id`` обнуляется, результаты остаются в статистике."""

    async def execute(self, uow: GameUnitOfWork, user_id: UUID) -> int:
        """Число обезличенных сессий."""
        count = await uow.sessions.forget_player(user_id)
        log.info("game.user_forgotten", sessions=count)
        return count
