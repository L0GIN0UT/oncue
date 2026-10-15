"""Исходники (аудио, кадры, постер, саундтрек, звук фильма, текст), из которых генерируются
ассеты уровней."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from gv_core.domain import Entity
from gv_core.errors import DomainError


class SourceKind(StrEnum):
    """Вид исходника (совпадает с ``source_media[].kind`` в событиях catalog)."""

    AUDIO = "audio"
    FRAME = "frame"
    POSTER = "poster"
    SOUNDTRACK = "soundtrack"
    AUDIO_CLIP = "audio_clip"
    # Тексты загадок (ADR 0024): содержимое приходит в событии catalog, а не файлом.
    TEXT = "text"


class SourceOrigin(StrEnum):
    """Происхождение исходника (D6 плана этапа 3a): загрузка, TMDB, синтетический засев;
    ``catalog`` — тексты загадок, одобренные модератором catalog (ADR 0024)."""

    UPLOAD = "upload"
    TMDB = "tmdb"
    SEED = "seed"
    CATALOG = "catalog"


# Происхождения файлов, которые админ загрузил сам: они важнее каталожных при выборе.
_ADMIN_ORIGINS = frozenset({SourceOrigin.UPLOAD, SourceOrigin.SEED})


class SourceStatus(StrEnum):
    """``registered`` — известен адрес; ``ready`` — нормализованная копия в хранилище."""

    REGISTERED = "registered"
    READY = "ready"
    FAILED = "failed"


class MediaFamily(StrEnum):
    """Семейство типа файла по содержимому."""

    AUDIO = "audio"
    IMAGE = "image"
    # Текст не загружается файлом: ни одна сигнатура не даёт этого семейства.
    TEXT = "text"


# Какое семейство файлов допустимо для вида исходника. Новый вид — новая строка, не ветка в коде.
FAMILY_BY_KIND: dict[SourceKind, MediaFamily] = {
    SourceKind.AUDIO: MediaFamily.AUDIO,
    SourceKind.FRAME: MediaFamily.IMAGE,
    SourceKind.POSTER: MediaFamily.IMAGE,
    SourceKind.SOUNDTRACK: MediaFamily.AUDIO,
    SourceKind.AUDIO_CLIP: MediaFamily.AUDIO,
    SourceKind.TEXT: MediaFamily.TEXT,
}

MIN_POSITION = 1
MAX_POSITION = 100


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
        position: Порядок среди исходников своего вида (кадры ``frame_sequence``:
            1 — самый сложный), 1..100.
        source_url: Откуда взят исходник (D6), для снятия по жалобе правообладателя.
        license_note: Основание использования (D6).
        text_content: Содержимое текстового исходника (``kind = text``) — JSON-объект без
            названия единицы; у остальных видов ``None``.
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
        position: int = MIN_POSITION,
        source_url: str | None = None,
        license_note: str | None = None,
        text_content: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(id)
        if not MIN_POSITION <= position <= MAX_POSITION:
            raise DomainError(
                f"position вне диапазона {MIN_POSITION}..{MAX_POSITION}", code="source.position"
            )
        if (kind is SourceKind.TEXT) != (text_content is not None):
            raise DomainError(
                "содержимое есть только у текстового исходника", code="source.text_content"
            )
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
        self.position = position
        self.source_url = source_url
        self.license_note = license_note
        self.text_content = text_content

    @property
    def is_stored(self) -> bool:
        """Нормализованная копия уже лежит в хранилище."""
        return self.status is SourceStatus.READY and self.object_key is not None

    @property
    def is_usable(self) -> bool:
        """Исходник можно брать в набор (не отбракован при загрузке)."""
        return self.status is not SourceStatus.FAILED

    @classmethod
    def text(
        cls,
        id: UUID,  # noqa: A002
        *,
        media_item_id: UUID,
        content: dict[str, Any],
        sha256: str,
        size_bytes: int,
        created_at: datetime,
    ) -> SourceMedia:
        """Текстовый исходник из события catalog: сразу ``ready``, без объекта в хранилище."""
        if size_bytes <= 0:
            raise DomainError("пустой исходник", code="source.empty")
        return cls(
            id,
            media_item_id=media_item_id,
            kind=SourceKind.TEXT,
            origin=SourceOrigin.CATALOG,
            uri=None,
            created_at=created_at,
            status=SourceStatus.READY,
            sha256=sha256,
            size_bytes=size_bytes,
            mime="application/json",
            text_content=content,
        )

    def move_to(self, position: int) -> None:
        """Сменить порядок исходника среди своего вида."""
        if not MIN_POSITION <= position <= MAX_POSITION:
            raise DomainError(
                f"position вне диапазона {MIN_POSITION}..{MAX_POSITION}", code="source.position"
            )
        self.position = position

    def mark_stored(self, *, object_key: str, sha256: str, size_bytes: int, mime: str) -> None:
        """Зафиксировать сохранённую нормализованную копию."""
        if size_bytes <= 0:
            raise DomainError("пустой исходник", code="source.empty")
        self.object_key = object_key
        self.sha256 = sha256
        self.size_bytes = size_bytes
        self.mime = mime
        self.status = SourceStatus.READY

    def priority(self) -> tuple[int, datetime, int]:
        """Порядок выбора исходника: загруженный админом важнее каталожного, затем свежее,
        затем меньшая позиция (у кадров одного импорта время одинаковое)."""
        return (1 if self.origin in _ADMIN_ORIGINS else 0, self.created_at, -self.position)


def best_source(sources: Sequence[SourceMedia], kind: SourceKind) -> SourceMedia | None:
    """Лучший исходник нужного вида (без отбракованных)."""
    candidates = [s for s in sources if s.kind is kind and s.is_usable]
    return max(candidates, key=SourceMedia.priority, default=None)


def sources_by_position(sources: Sequence[SourceMedia], kind: SourceKind) -> dict[int, SourceMedia]:
    """Лучший исходник вида на каждой позиции (без отбракованных)."""
    best: dict[int, SourceMedia] = {}
    for source in sources:
        if source.kind is not kind or not source.is_usable:
            continue
        current = best.get(source.position)
        if current is None or source.priority() > current.priority():
            best[source.position] = source
    return best
