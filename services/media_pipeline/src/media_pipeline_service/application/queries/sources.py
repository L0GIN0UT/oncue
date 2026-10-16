"""Исходники единицы для карточки в админке (``GET /admin/v1/sources``, хвост 3a)."""

from __future__ import annotations

from uuid import UUID

from media_pipeline_service.application.dto import SourceListView
from media_pipeline_service.application.ports import UnitOfWorkFactory


class ListSources:
    """Каталожные исходники единицы: по виду, затем по ``position``.

    Исходники своих загадок сборников (``scope``, ADR 0029) сюда не попадают: это UGC
    автора, их видно в админке collections, а здесь — то, что админ загрузил или пришло из
    catalog. Ссылок на объекты S3 нет.
    """

    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self._uow = uow

    async def execute(self, media_item_id: UUID) -> list[SourceListView]:
        """Прочитать (без блокировок); неизвестная единица — пустой список."""
        async with self._uow() as uow:
            sources = await uow.sources.list_for_item(media_item_id)
        ordered = sorted(sources, key=lambda s: (s.kind.value, s.position, s.created_at, s.id))
        return [SourceListView.of(s) for s in ordered]
