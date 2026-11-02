"""Данные, которыми application обменивается с адаптерами и API."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from catalog_service.domain.clue_texts import ClueTexts
from catalog_service.domain.media_item import MediaItem
from catalog_service.domain.model import (
    AliasDraft,
    ExternalSource,
    MediaDomain,
    MediaKind,
    MediaMetrics,
    PersonRole,
    SourceMediaDraft,
)

# Порядок поиска уже существующей единицы при импорте: сначала самые надёжные ключи.
EXTERNAL_MATCH_ORDER: tuple[ExternalSource, ...] = (
    ExternalSource.TMDB,
    ExternalSource.MUSICBRAINZ,
    ExternalSource.SPOTIFY,
    ExternalSource.YANDEX_MUSIC,
    ExternalSource.ISRC,
    ExternalSource.SEED,
)


@dataclass(frozen=True, slots=True)
class PersonRef:
    """Персона из внешнего источника до сопоставления с каталогом.

    Attributes:
        key: Стабильный ключ персоны: ``tmdb:525``, ``musicbrainz:<mbid>`` или
            ``name:<нормализованное имя>``, если у источника нет id.
    """

    key: str
    name: str
    role: PersonRole
    order: int = 0


@dataclass(frozen=True, slots=True)
class ImportedClueTexts:
    """Тексты загадок из источника (ADR 0024).

    Attributes:
        plot_raw: Исходный сюжет (TMDB ``overview``); маскируется автоматически.
        plot_masked: Уже замаскированный сюжет (файл засева); ``None`` — маскировать
            ``plot_raw``.
    """

    plot_raw: str | None = None
    plot_masked: str | None = None
    tagline: str | None = None
    emoji: str | None = None


@dataclass(frozen=True, slots=True)
class ImportedItem:
    """Единица, как её отдал источник метаданных (TMDB, файл, MusicBrainz…).

    Attributes:
        texts_authoritative: Тексты загадок заменяют текущие и сразу одобряются (только
            синтетический засев в local/ci, ``GV_CATALOG__SEED_TEXTS_APPROVED``).
    """

    kind: MediaKind
    title: str
    original_title: str | None = None
    year: int | None = None
    aliases: tuple[AliasDraft, ...] = ()
    persons: tuple[PersonRef, ...] = ()
    genres: tuple[str, ...] = ()
    external_refs: dict[ExternalSource, str] = field(default_factory=dict)
    metrics: MediaMetrics = field(default_factory=MediaMetrics)
    source_media: tuple[SourceMediaDraft, ...] = ()
    countries: tuple[str, ...] = ()
    clue_texts: ImportedClueTexts | None = None
    texts_authoritative: bool = False


@dataclass(frozen=True, slots=True)
class Suggestion:
    """Строка автокомплита и поиска."""

    id: uuid.UUID
    domain: MediaDomain
    title: str
    year: int | None
    subtitle: str | None
    creator: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Для кэша."""
        return {
            "id": str(self.id),
            "domain": self.domain.value,
            "title": self.title,
            "year": self.year,
            "subtitle": self.subtitle,
            "creator": self.creator,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Suggestion:
        """Из кэша."""
        return cls(
            id=uuid.UUID(str(data["id"])),
            domain=MediaDomain(data["domain"]),
            title=str(data["title"]),
            year=None if data.get("year") is None else int(data["year"]),
            subtitle=None if data.get("subtitle") is None else str(data["subtitle"]),
            creator=None if data.get("creator") is None else str(data["creator"]),
        )


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Нормализованный поисковый запрос."""

    normalized: str
    domain: MediaDomain | None
    limit: int
    scope_person: uuid.UUID | None = None


_SEARCHABLE_ROLES = frozenset({PersonRole.ARTIST, PersonRole.DIRECTOR})


@dataclass(frozen=True, slots=True)
class SearchDocument:
    """Документ поискового индекса — проекция единицы."""

    id: uuid.UUID
    version: int
    domain: MediaDomain
    kind: MediaKind
    status: str
    title: str
    original_title: str | None
    year: int | None
    subtitle: str | None
    aliases: tuple[str, ...]
    popularity: float
    # Исполнители и режиссёры: имена — для поиска по персоне, id — для области подсказок.
    people: tuple[str, ...] = ()
    person_ids: tuple[str, ...] = ()
    creator: str | None = None

    @classmethod
    def from_item(cls, item: MediaItem) -> SearchDocument:
        """Собрать документ из агрегата."""
        searchable = [p for p in item.persons if p.role in _SEARCHABLE_ROLES]
        return cls(
            id=item.id,
            version=item.version,
            domain=item.domain,
            kind=item.kind,
            status=item.status.value,
            title=item.title,
            original_title=item.original_title,
            year=item.year,
            subtitle=item.subtitle,
            aliases=tuple(a.normalized for a in item.aliases),
            popularity=item.metrics.popularity or 0.0,
            people=tuple(dict.fromkeys(p.name for p in searchable)),
            person_ids=tuple(dict.fromkeys(str(p.person_id) for p in searchable)),
            creator=item.creator,
        )


@dataclass(frozen=True, slots=True)
class ItemSummary:
    """Строка списка единиц в админке."""

    id: uuid.UUID
    domain: MediaDomain
    kind: MediaKind
    title: str
    year: int | None
    status: str
    version: int


@dataclass(frozen=True, slots=True)
class AdminItemFilter:
    """Фильтр списка единиц в админке."""

    normalized_q: str | None = None
    domain: MediaDomain | None = None
    status: str | None = None


@dataclass(frozen=True, slots=True)
class RecordedAction:
    """Выполненная админ-мутация: аудит и ответ для повтора по ``Idempotency-Key``."""

    id: uuid.UUID
    actor_id: uuid.UUID
    idempotency_key: str
    action: str
    target_id: uuid.UUID
    reason: str
    request_hash: str
    response: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ClueStatus:
    """Состояние ассетов загадки для единицы (проекция событий media-pipeline)."""

    media_item_id: uuid.UUID
    clue_type: str
    profile_key: str
    asset_set_id: uuid.UUID
    status: str
    version: int
    levels_count: int | None = None
    error_code: str | None = None


def admin_item_view(item: MediaItem) -> dict[str, Any]:
    """Карточка единицы для admin-API (JSON-совместимая; хранится для идемпотентности)."""
    snapshot = item.snapshot()
    return {
        "id": str(item.id),
        "domain": snapshot["domain"],
        "kind": snapshot["kind"],
        "title": item.title,
        "original_title": item.original_title,
        "year": item.year,
        "status": item.status.value,
        "blocked_reason_code": item.blocked_reason_code,
        "version": item.version,
        "subtitle": item.subtitle,
        "aliases": [{**a.to_contract(), "source": a.source.value} for a in item.aliases],
        "persons": snapshot["persons"],
        "genres": snapshot["genres"],
        "external_refs": {src.value: ref for src, ref in sorted(item.external_refs.items())},
        "metrics": snapshot["metrics"],
        "source_media": snapshot["source_media"],
        "countries": list(item.countries),
    }


def clue_texts_view(texts: ClueTexts) -> dict[str, Any]:
    """Тексты загадок для admin-API (JSON-совместимые; хранятся для идемпотентности)."""
    return {
        "plot_raw": texts.plot_raw,
        "plot_masked": texts.plot_masked,
        "tagline": texts.tagline,
        "emoji": texts.emoji,
        "status": texts.status.value,
        "revision": texts.revision,
        "approved_revision": texts.approved_revision,
    }
