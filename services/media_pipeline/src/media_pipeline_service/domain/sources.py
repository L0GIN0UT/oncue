"""Исходные медиа (аудио, кадр, постер), из которых генерируются ассеты уровней."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from gv_core.domain import Entity
from gv_core.errors import DomainError


class SourceKind(StrEnum):
    """Вид исходника (совпадает с ``source_media[].kind`` в событиях catalog)."""

    AUDIO = "audio"
    FRAME = "frame"
    POSTER = "poster"


class SourceOrigin(StrEnum):
    """Откуда исходник: загружен админом или указан каталогом (TMDB)."""

    UPLOAD = "upload"
    TMDB = "tmdb"


class SourceStatus(StrEnum):
    """``registered`` — известен адрес; ``ready`` — нормализованная копия в хранилище."""

    REGISTERED = "registered"
    READY = "ready"
    FAILED = "failed"


class MediaFamily(StrEnum):
    """Семейство типа файла по содержимому."""

    AUDIO = "audio"
    IMAGE = "image"


# Какое семейство файлов допустимо для вида исходника. Новый вид — новая строка, не ветка в коде.
FAMILY_BY_KIND: dict[SourceKind, MediaFamily] = {
    SourceKind.AUDIO: MediaFamily.AUDIO,
    SourceKind.FRAME: MediaFamily.IMAGE,
    SourceKind.POSTER: MediaFamily.IMAGE,
}


@dataclass(frozen=True, slots=True)
class DetectedType:
    """Тип файла, распознанный по сигнатуре."""

    name: str
    family: MediaFamily


def _is_riff(head: bytes, form: bytes) -> bool:
    return head[:4] == b"RIFF" and head[8:12] == form


_MPEG_SYNC_HI = 0xFF
_MPEG_SYNC_LO = 0xE0


def _is_mp3(head: bytes) -> bool:
    # ID3-тег или синхрослово MPEG-кадра (11 единичных бит).
    return head[:3] == b"ID3" or (
        len(head) > 1 and head[0] == _MPEG_SYNC_HI and head[1] & _MPEG_SYNC_LO == _MPEG_SYNC_LO
    )


_SIGNATURES: tuple[tuple[str, MediaFamily, Callable[[bytes], bool]], ...] = (
    ("jpeg", MediaFamily.IMAGE, lambda h: h[:3] == b"\xff\xd8\xff"),
    ("png", MediaFamily.IMAGE, lambda h: h[:8] == b"\x89PNG\r\n\x1a\n"),
    ("webp", MediaFamily.IMAGE, lambda h: _is_riff(h, b"WEBP")),
    ("wav", MediaFamily.AUDIO, lambda h: _is_riff(h, b"WAVE")),
    ("flac", MediaFamily.AUDIO, lambda h: h[:4] == b"fLaC"),
    ("ogg", MediaFamily.AUDIO, lambda h: h[:4] == b"OggS"),
    ("mp3", MediaFamily.AUDIO, _is_mp3),
    ("mp4", MediaFamily.AUDIO, lambda h: h[4:8] == b"ftyp"),
)


def detect_type(head: bytes) -> DetectedType | None:
    """Распознать тип по первым байтам файла (расширению и Content-Type не доверяем)."""
    for name, family, matches in _SIGNATURES:
        if matches(head):
            return DetectedType(name, family)
    return None


class SourceMedia(Entity[UUID]):
    """Исходник медиа-единицы.

    Attributes:
        media_item_id: Единица каталога.
        kind: Вид исходника.
        origin: Происхождение.
        uri: Адрес у внешнего источника (для ``tmdb``), для загрузки — ``None``.
        object_key: Ключ нормализованной копии в бакете исходников.
        sha256: Хэш нормализованной копии.
        size_bytes: Размер нормализованной копии.
        mime: MIME нормализованной копии.
        status: Состояние.
        created_at: Когда исходник появился (для выбора самого свежего).
    """

    def __init__(
        self,
        id: UUID,  # noqa: A002
        *,
        media_item_id: UUID,
        kind: SourceKind,
        origin: SourceOrigin,
        uri: str | None,
        created_at: datetime,
        status: SourceStatus = SourceStatus.REGISTERED,
        object_key: str | None = None,
        sha256: str | None = None,
        size_bytes: int | None = None,
        mime: str | None = None,
    ) -> None:
        super().__init__(id)
        self.media_item_id = media_item_id
        self.kind = kind
        self.origin = origin
        self.uri = uri
        self.created_at = created_at
        self.status = status
        self.object_key = object_key
        self.sha256 = sha256
        self.size_bytes = size_bytes
        self.mime = mime

    @property
    def is_stored(self) -> bool:
        """Нормализованная копия уже лежит в хранилище."""
        return self.status is SourceStatus.READY and self.object_key is not None

    def mark_stored(self, *, object_key: str, sha256: str, size_bytes: int, mime: str) -> None:
        """Зафиксировать сохранённую нормализованную копию."""
        if size_bytes <= 0:
            raise DomainError("пустой исходник", code="source.empty")
        self.object_key = object_key
        self.sha256 = sha256
        self.size_bytes = size_bytes
        self.mime = mime
        self.status = SourceStatus.READY

    def priority(self) -> tuple[int, datetime]:
        """Порядок выбора исходника: загруженный админом важнее каталожного, затем свежее."""
        return (1 if self.origin is SourceOrigin.UPLOAD else 0, self.created_at)


def best_source(sources: list[SourceMedia], kind: SourceKind) -> SourceMedia | None:
    """Лучший исходник нужного вида (без отбракованных)."""
    candidates = [s for s in sources if s.kind is kind and s.status is not SourceStatus.FAILED]
    return max(candidates, key=SourceMedia.priority, default=None)
