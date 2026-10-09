"""Список наборов ассетов единицы (``GET /internal/v1/asset-sets``)."""

from __future__ import annotations

from uuid import UUID

from media_pipeline_service.application.dto import AssetSetView
from media_pipeline_service.application.ports import UnitOfWorkFactory


class ListAssetSets:
    """Наборы единицы, опционально одного типа загадки."""

    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self._uow = uow

    async def execute(
        self, media_item_id: UUID, clue_type: str | None = None
    ) -> list[AssetSetView]:
        """Прочитать наборы (без блокировок)."""
        async with self._uow() as uow:
            sets = await uow.asset_sets.list_for_item(media_item_id, clue_type)
        return [AssetSetView.of(s) for s in sets]
