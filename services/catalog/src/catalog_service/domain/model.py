"""Value objects и перечисления каталога."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from gv_core.errors import DomainError, InvalidInputError

MIN_YEAR = 1870
FUTURE_YEARS = 2
MAX_TITLE_LEN = 300
MAX_GENRE_LEN = 64

# Пространство имён для детерминированных id исходников (uuid5): одна и та же пара
# (единица, kind, uri) при повторном импорте получает тот же id — на него ссылается
# media-pipeline, и его нельзя «перевыпускать».
_SOURCE_MEDIA_NS = uuid.UUID("0192a3b4-c5d6-7e8f-9a0b-5f1c0000c0de")


class MediaDomain(StrEnum):
    """Предметная область единицы."""

    MUSIC = "music"
    FILM = "film"


class MediaKind(StrEnum):
    """Вид единицы. Сериалы и аниме — этап 3."""

    TRACK = "track"
    FILM = "film"


KIND_DOMAIN: dict[MediaKind, MediaDomain] = {
    MediaKind.TRACK: MediaDomain.MUSIC,
    MediaKind.FILM: MediaDomain.FILM,
}


class ItemStatus(StrEnum):
    """Статус единицы: заблокированная не попадает в выдачу и игры."""

    ACTIVE = "active"
    BLOCKED = "blocked"


class AliasKind(StrEnum):
    """Вид алиаса (контракт событий ``aliases[].kind``)."""

    TITLE = "title"
    ALT = "alt"
    TRANSLIT = "translit"
    SHORT = "short"


class AliasSource(StrEnum):
    """Откуда алиас: импорт перезаписывается следующим импортом, ручной — никогда."""

    IMPORT = "import"
    MANUAL = "manual"


class PersonRole(StrEnum):
    """Роль персоны в единице."""

    ARTIST = "artist"
    DIRECTOR = "director"
    ACTOR = "actor"


class SourceMediaKind(StrEnum):
    """Вид исходника для загадок."""

    AUDIO = "audio"
    FRAME = "frame"
    POSTER = "poster"


class SourceMediaOrigin(StrEnum):
    """Происхождение исходника. Аудио — только ``upload`` (ADR 0014)."""

    UPLOAD = "upload"
    TMDB = "tmdb"


class ExternalSource(StrEnum):
    """Источники внешних идентификаторов.

    ``seed`` — ключ строки файла импорта; во внешний контракт событий он не попадает.
    """

    TMDB = "tmdb"
    MUSICBRAINZ = "musicbrainz"
    ISRC = "isrc"
    SPOTIFY = "spotify"
    YANDEX_MUSIC = "yandex_music"
    SEED = "seed"


# Источники, которые разрешены в ``external_refs`` событий (contracts/events/catalog).
PUBLISHED_EXTERNAL_SOURCES: frozenset[ExternalSource] = frozenset(
    {
        ExternalSource.TMDB,
        ExternalSource.MUSICBRAINZ,
        ExternalSource.ISRC,
        ExternalSource.SPOTIFY,
        ExternalSource.YANDEX_MUSIC,
    }
)


@dataclass(frozen=True, slots=True)
class Alias:
    """Вариант названия, по которому единицу можно найти и угадать.

    Attributes:
        text: Как показывать.
        normalized: Каноническая форма (``TitleNormalizer``) — ключ уникальности в единице.
        lang: Язык (ISO 639-1) или ``None``.
        kind: Вид алиаса.
        source: Импорт или ручная правка модератора.
    """

    text: str
    normalized: str
    lang: str | None
    kind: AliasKind
    source: AliasSource

    def to_contract(self) -> dict[str, Any]:
        """Представление для событий."""
        return {
            "text": self.text,
            "normalized": self.normalized,
            "lang": self.lang,
            "kind": self.kind.value,
        }


@dataclass(frozen=True, slots=True)
class AliasDraft:
    """Алиас до нормализации (из импорта или запроса модератора)."""

    text: str
    kind: AliasKind
    lang: str | None = None


@dataclass(frozen=True, slots=True)
class PersonCredit:
    """Участие персоны в единице."""

    person_id: uuid.UUID
    name: str
    role: PersonRole
    order: int

    def to_contract(self) -> dict[str, Any]:
        """Представление для событий."""
        return {
            "person_id": str(self.person_id),
            "name": self.name,
            "role": self.role.value,
            "order": self.order,
        }


@dataclass(frozen=True, slots=True)
class MediaMetrics:
    """Метрики для режимов «больше/меньше» и ранжирования поиска. ``None`` — неизвестно."""

    popularity: float | None = None
    vote_average: float | None = None
    vote_count: int | None = None
    revenue_usd: int | None = None
    runtime_min: int | None = None
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        for name in ("popularity", "vote_count", "revenue_usd", "runtime_min", "duration_ms"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise InvalidInputError(
                    f"метрика {name} не может быть отрицательной", code="catalog.invalid_metrics"
                )
        if self.vote_average is not None and not 0 <= self.vote_average <= 10:  # noqa: PLR2004
            raise InvalidInputError("vote_average вне 0..10", code="catalog.invalid_metrics")

    def to_contract(self) -> dict[str, Any]:
        """Только известные значения (в контракте поля необязательные и не-null)."""
        return {
            name: getattr(self, name)
            for name in (
                "popularity",
                "vote_average",
                "vote_count",
                "revenue_usd",
                "runtime_min",
                "duration_ms",
            )
            if getattr(self, name) is not None
        }

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> MediaMetrics:
        """Собрать из словаря (JSONB, файл импорта); неизвестные ключи игнорируются."""
        return cls(
            popularity=_opt_float(data.get("popularity")),
            vote_average=_opt_float(data.get("vote_average")),
            vote_count=_opt_int(data.get("vote_count")),
            revenue_usd=_opt_int(data.get("revenue_usd")),
            runtime_min=_opt_int(data.get("runtime_min")),
            duration_ms=_opt_int(data.get("duration_ms")),
        )


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


@dataclass(frozen=True, slots=True)
class SourceMedia:
    """Исходник для загадок (постер, кадр, аудио)."""

    id: uuid.UUID
    kind: SourceMediaKind
    origin: SourceMediaOrigin
    uri: str

    def to_contract(self) -> dict[str, Any]:
        """Представление для событий."""
        return {
            "source_media_ref": str(self.id),
            "kind": self.kind.value,
            "origin": self.origin.value,
            "uri": self.uri,
        }


@dataclass(frozen=True, slots=True)
class SourceMediaDraft:
    """Исходник из импорта (id назначает агрегат)."""

    kind: SourceMediaKind
    origin: SourceMediaOrigin
    uri: str

    def materialize(self, media_item_id: uuid.UUID) -> SourceMedia:
        """Исходник с детерминированным id (uuid5 от единицы, вида и адреса)."""
        ref = uuid.uuid5(_SOURCE_MEDIA_NS, f"{media_item_id}:{self.kind.value}:{self.uri}")
        return SourceMedia(id=ref, kind=self.kind, origin=self.origin, uri=self.uri)


@dataclass(frozen=True, slots=True)
class ItemData:
    """Метаданные единицы из импорта — вход для создания и обновления агрегата.

    Персоны уже сопоставлены с id каталога (это делает use-case через репозиторий).
    """

    kind: MediaKind
    title: str
    original_title: str | None = None
    year: int | None = None
    aliases: tuple[AliasDraft, ...] = ()
    persons: tuple[PersonCredit, ...] = ()
    genres: tuple[str, ...] = ()
    external_refs: dict[ExternalSource, str] = field(default_factory=dict)
    metrics: MediaMetrics = field(default_factory=MediaMetrics)
    source_media: tuple[SourceMediaDraft, ...] = ()

    @property
    def domain(self) -> MediaDomain:
        """Область по виду единицы."""
        return KIND_DOMAIN[self.kind]


def validate_year(year: int | None, current_year: int) -> None:
    """Год выпуска в допустимом диапазоне (1870..текущий+2) или неизвестен."""
    if year is None:
        return
    if not MIN_YEAR <= year <= current_year + FUTURE_YEARS:
        raise DomainError(
            f"год {year} вне диапазона {MIN_YEAR}..{current_year + FUTURE_YEARS}",
            code="catalog.invalid_year",
        )


def validate_title(title: str) -> str:
    """Название непустое и не длиннее лимита контракта."""
    value = title.strip()
    if not value or len(value) > MAX_TITLE_LEN:
        raise DomainError("недопустимое название", code="catalog.invalid_title")
    return value


def normalize_genre(genre: str) -> str | None:
    """Жанр как slug в нижнем регистре; пустые и слишком длинные отбрасываются."""
    value = "-".join(genre.strip().casefold().split())
    if not value or len(value) > MAX_GENRE_LEN:
        return None
    return value
