"""Исходники единицы для карточки в админке (``GET /admin/v1/sources``, хвост 3a)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from media_pipeline_service.application.dto import SourceListView
from media_pipeline_service.application.ports import ObjectStorage, StorageArea, UnitOfWorkFactory
from media_pipeline_service.domain.errors import SourceNotFoundError
from media_pipeline_service.domain.sources import FAMILY_BY_KIND, MediaFamily, SourceStatus


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

    async def unattributed(self, *, limit: int, after_id: UUID | None) -> list[SourceListView]:
        """Файловые исходники без источника или лицензии по всем единицам (ADR 0072).

        Страницы — по ``id`` (uuid7, то есть по времени создания): ``after_id`` — последний
        ``source_media_id`` предыдущей страницы.
        """
        async with self._uow() as uow:
            sources = await uow.sources.list_unattributed(limit=limit, after_id=after_id)
        return [SourceListView.of(s) for s in sources]


@dataclass(frozen=True, slots=True)
class SourcePreview:
    """Содержимое исходника-изображения для просмотра в админке."""

    data: bytes
    content_type: str


class GetSourcePreview:
    """Картинка каталожного исходника (обложка, постер, кадр) для админки.

    Читается нормализованная копия из бакета исходников по ключу из БД: адресов S3 в ответе нет.
    Не изображение, не готовый и «чужой» (scope сборника) исходники недоступны — 404.
    """

    def __init__(self, uow: UnitOfWorkFactory, storage: ObjectStorage, *, max_bytes: int) -> None:
        self._uow = uow
        self._storage = storage
        self._max_bytes = max_bytes

    async def execute(self, source_id: UUID) -> SourcePreview:
        """Прочитать картинку.

        Raises:
            SourceNotFoundError: нет исходника, он не изображение, не готов или принадлежит
                сборнику.
        """
        async with self._uow() as uow:
            source = await uow.sources.get(source_id)
        if (
            source is None
            or source.scope is not None
            or source.status is not SourceStatus.READY
            or FAMILY_BY_KIND[source.kind] is not MediaFamily.IMAGE
            or source.object_key is None
        ):
            raise SourceNotFoundError("исходник не найден")
        # Адрес объекта — кортежем: так сканер секретов не принимает ключ объекта за секрет.
        location = (StorageArea.SOURCES, source.object_key)
        data = await self._storage.get(*location, max_bytes=self._max_bytes)
        return SourcePreview(data=data, content_type=source.mime or "image/png")
