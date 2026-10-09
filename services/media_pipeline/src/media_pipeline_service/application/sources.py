"""Приём исходника: проверка сигнатуры и размера, перекодирование, сохранение в бакет исходников."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import structlog

from media_pipeline_service.application.ports import (
    ObjectStorage,
    SourceNormalizer,
    StorageArea,
    TokenSource,
)
from media_pipeline_service.domain.errors import SourceTooLargeError, UnsupportedSourceError
from media_pipeline_service.domain.links import source_object_key
from media_pipeline_service.domain.sources import FAMILY_BY_KIND, SourceKind, detect_type

log = structlog.get_logger(__name__)

_SIGNATURE_BYTES = 16


@dataclass(frozen=True, slots=True)
class StoredSource:
    """Нормализованный исходник в хранилище (``data`` — для немедленной генерации)."""

    object_key: str
    sha256: str
    size_bytes: int
    mime: str
    data: bytes


class SourceIngestor:
    """Проверить и сохранить исходник.

    Сырые байты не сохраняются никогда: только перекодированная копия без метаданных —
    так из бакета не утекут EXIF/теги, а «полиглоты» и битые файлы отсекаются декодером.
    """

    def __init__(
        self,
        storage: ObjectStorage,
        normalizers: Mapping[SourceKind, SourceNormalizer],
        tokens: TokenSource,
        *,
        max_bytes: int,
    ) -> None:
        self._storage = storage
        self._normalizers = normalizers
        self._tokens = tokens
        self._max_bytes = max_bytes

    def check(self, kind: SourceKind, data: bytes) -> None:
        """Проверить размер и сигнатуру до перекодирования."""
        if len(data) > self._max_bytes:
            raise SourceTooLargeError(
                "исходник больше лимита", details={"max_bytes": self._max_bytes}
            )
        detected = detect_type(data[:_SIGNATURE_BYTES])
        if detected is None or detected.family is not FAMILY_BY_KIND[kind]:
            raise UnsupportedSourceError(f"файл не подходит для исходника вида {kind.value}")

    async def ingest(self, kind: SourceKind, data: bytes) -> StoredSource:
        """Проверить, перекодировать и сохранить."""
        self.check(kind, data)
        normalized = await self._normalizers[kind].normalize(data)
        key = source_object_key(self._tokens.token_hex(), normalized.ext)
        await self._storage.put(StorageArea.SOURCES, key, normalized.data, normalized.mime)
        return StoredSource(
            object_key=key,
            sha256=hashlib.sha256(normalized.data).hexdigest(),
            size_bytes=len(normalized.data),
            mime=normalized.mime,
            data=normalized.data,
        )


async def delete_quietly(storage: ObjectStorage, area: StorageArea, keys: Sequence[str]) -> None:
    """Удалить объекты после коммита; сбой не отменяет операцию (останется мусор, не утечка).

    Объекты без строки в ``asset_levels`` недоступны: ссылку выдают только по строке в БД.
    """
    if not keys:
        return
    try:
        await storage.delete(area, keys)
    except Exception:
        log.warning("storage.delete_failed", area=area.value, count=len(keys), exc_info=True)
