"""Реакции на события: индексация своих единиц и отметки о готовности ассетов."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog

from catalog_service.application.dto import ClueStatus, SearchDocument
from catalog_service.application.ports import (
    ClueStatusRepository,
    MediaItemRepository,
    PrefixCache,
    SearchIndex,
)
from gv_core.errors import DependencyUnavailableError

log = structlog.get_logger(__name__)


class IndexItem:
    """Записать текущее состояние единицы в поисковый индекс.

    Индексируется не payload события, а актуальная строка БД с её версией: так события
    ``Blocked``/``Unblocked`` (без полного снимка) обрабатываются тем же кодом, а
    устаревшие и повторные события отбрасывает сам индекс (``version_type=external``).
    """

    def __init__(
        self,
        items: MediaItemRepository,
        index: SearchIndex | None,
        cache: PrefixCache | None,
    ) -> None:
        self._items = items
        self._index = index
        self._cache = cache

    async def __call__(self, item_id: uuid.UUID, event_version: int) -> bool:
        """Проиндексировать.

        Returns:
            ``True`` — индекс обновлён; ``False`` — индекс выключен, единицы нет или
            в индексе уже не меньшая версия.

        Raises:
            DependencyUnavailableError: ``catalog.index_lag`` — строка БД отстаёт от
                события; событие будет повторено.
        """
        if self._index is None:
            return False
        item = await self._items.get(item_id)
        if item is None:
            log.warning("catalog.index_item_missing", media_item_id=str(item_id))
            return False
        if item.version < event_version:
            # Строка БД не может отставать от своего же события (оно пишется в той же
            # транзакции). Если так — читаем реплику с лагом; повторим позже.
            raise DependencyUnavailableError(
                "версия в БД меньше версии события", code="catalog.index_lag"
            )
        updated = await self._index.index(SearchDocument.from_item(item))
        if updated and self._cache is not None:
            await self._cache.invalidate()
        return updated


@dataclass(frozen=True, slots=True)
class ClueAssetsEvent:
    """Событие media-pipeline о наборе ассетов (``AssetsReady``/``AssetsFailed``)."""

    media_item_id: uuid.UUID
    asset_set_id: uuid.UUID
    clue_type: str
    profile_key: str
    status: str
    version: int
    levels_count: int | None = None
    error_code: str | None = None


class MarkClueAssets:
    """Обновить проекцию готовности ассетов загадки."""

    def __init__(self, items: MediaItemRepository, statuses: ClueStatusRepository) -> None:
        self._items = items
        self._statuses = statuses

    async def __call__(self, event: ClueAssetsEvent) -> bool:
        """Применить событие.

        ``version`` монотонна по ``media_item_id`` (контракт media-pipeline), поэтому
        событие с ``version`` не больше применённой к этой загадке отбрасывается
        **независимо от** ``asset_set_id``: запоздавшее событие перегенерации старого
        набора не вытесняет более новый набор.

        Returns:
            ``True``, если проекция изменилась.
        """
        if not await self._items.exists(event.media_item_id):
            log.warning("catalog.clue_assets_unknown_item", media_item_id=str(event.media_item_id))
            return False
        current = await self._statuses.get(event.media_item_id, event.clue_type, event.profile_key)
        if current is not None and current.version >= event.version:
            log.info(
                "catalog.clue_assets_stale",
                media_item_id=str(event.media_item_id),
                asset_set_id=str(event.asset_set_id),
                version=event.version,
                applied=current.version,
            )
            return False
        await self._statuses.upsert(
            ClueStatus(
                media_item_id=event.media_item_id,
                clue_type=event.clue_type,
                profile_key=event.profile_key,
                asset_set_id=event.asset_set_id,
                status=event.status,
                version=event.version,
                levels_count=event.levels_count,
                error_code=event.error_code,
            )
        )
        return True
