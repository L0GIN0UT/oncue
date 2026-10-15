"""Агрегат ``MediaItem`` — медиа-единица каталога (трек или фильм)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from catalog_service.domain.clue_texts import ApprovedClueTexts
from catalog_service.domain.events import (
    MediaItemBlocked,
    MediaItemCreated,
    MediaItemUnblocked,
    MediaItemUpdated,
)
from catalog_service.domain.model import (
    MAX_TITLE_LEN,
    PUBLISHED_EXTERNAL_SOURCES,
    Alias,
    AliasDraft,
    AliasKind,
    AliasSource,
    ExternalSource,
    ItemData,
    ItemStatus,
    MediaDomain,
    MediaKind,
    MediaMetrics,
    PersonCredit,
    PersonRole,
    SourceMedia,
    normalize_countries,
    normalize_genre,
    validate_title,
    validate_year,
)
from catalog_service.domain.normalization import TitleNormalizer
from gv_core.domain import AggregateRoot
from gv_core.errors import ConflictError, DomainError

REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_.]*$")
MAX_REASON_CODE_LEN = 64
MAX_ALIASES = 100

# Поля снимка, изменения которых попадают в ``MediaItemUpdated.changed_fields``.
_TRACKED_FIELDS = (
    "title",
    "original_title",
    "year",
    "aliases",
    "persons",
    "genres",
    "external_refs",
    "metrics",
    "source_media",
    "countries",
    "clue_texts",
)
# changed_fields переопубликации (RepublishItems): данные не менялись, снимок выпущен заново.
REPUBLISHED_FIELD = "republish"


@dataclass(frozen=True, slots=True)
class _Content:
    """Изменяемое содержимое единицы (всё, кроме id, вида, статуса и версии)."""

    title: str
    original_title: str | None
    year: int | None
    aliases: tuple[Alias, ...]
    persons: tuple[PersonCredit, ...]
    genres: tuple[str, ...]
    external_refs: tuple[tuple[ExternalSource, str], ...]
    metrics: MediaMetrics
    source_media: tuple[SourceMedia, ...]
    countries: tuple[str, ...] = ()
    clue_texts: ApprovedClueTexts = field(default_factory=ApprovedClueTexts)


class MediaItem(AggregateRoot[uuid.UUID]):
    """Медиа-единица.

    Инварианты:
        * у единицы ≥ 1 алиас вида ``title``;
        * год выпуска — 1870..текущий+2 или неизвестен;
        * ручные алиасы (``source=manual``) импорт не удаляет и не перезаписывает;
        * каждое изменение увеличивает ``version`` на 1 и фиксирует событие с этой версией —
          по ней потребители отбрасывают устаревшие события.

    Attributes:
        persisted_version: Версия, с которой агрегат загружен из хранилища (0 — новый);
            репозиторий использует её для оптимистичной блокировки.
    """

    def __init__(
        self,
        id: uuid.UUID,  # noqa: A002 — поле домена называется id
        *,
        kind: MediaKind,
        status: ItemStatus,
        version: int,
        content: _Content,
        blocked_reason_code: str | None = None,
    ) -> None:
        super().__init__(id, version)
        self.kind = kind
        self.status = status
        self.blocked_reason_code = blocked_reason_code
        self._content = content
        self.persisted_version = version

    # ------------------------------------------------------------------ фабрики

    @classmethod
    def create(
        cls,
        id: uuid.UUID,  # noqa: A002
        data: ItemData,
        normalizer: TitleNormalizer,
        *,
        current_year: int,
    ) -> MediaItem:
        """Создать единицу из метаданных импорта (версия 1, статус ``active``)."""
        content = _build_content(
            id, data, normalizer, current_year, keep=(), keep_texts=ApprovedClueTexts()
        )
        item = cls(id, kind=data.kind, status=ItemStatus.ACTIVE, version=0, content=content)
        item.version = 1
        item.record(MediaItemCreated(aggregate_id=str(id), snapshot=item.snapshot()))
        return item

    @classmethod
    def restore(
        cls,
        id: uuid.UUID,  # noqa: A002
        *,
        kind: MediaKind,
        status: ItemStatus,
        version: int,
        title: str,
        original_title: str | None,
        year: int | None,
        aliases: Iterable[Alias],
        persons: Iterable[PersonCredit],
        genres: Iterable[str],
        external_refs: dict[ExternalSource, str],
        metrics: MediaMetrics,
        source_media: Iterable[SourceMedia],
        blocked_reason_code: str | None = None,
        countries: Iterable[str] = (),
        clue_texts: ApprovedClueTexts | None = None,
    ) -> MediaItem:
        """Восстановить агрегат из хранилища (без событий и проверок импорта)."""
        content = _Content(
            title=title,
            original_title=original_title,
            year=year,
            aliases=tuple(aliases),
            persons=tuple(sorted(persons, key=_person_key)),
            genres=tuple(genres),
            external_refs=tuple(sorted(external_refs.items())),
            metrics=metrics,
            source_media=tuple(source_media),
            countries=tuple(countries),
            clue_texts=clue_texts or ApprovedClueTexts(),
        )
        return cls(
            id,
            kind=kind,
            status=status,
            version=version,
            content=content,
            blocked_reason_code=blocked_reason_code,
        )

    # ------------------------------------------------------------------ свойства

    @property
    def domain(self) -> MediaDomain:
        """Предметная область."""
        return MediaDomain.MUSIC if self.kind is MediaKind.TRACK else MediaDomain.FILM

    @property
    def title(self) -> str:
        """Основное название."""
        return self._content.title

    @property
    def original_title(self) -> str | None:
        """Оригинальное название."""
        return self._content.original_title

    @property
    def year(self) -> int | None:
        """Год выпуска."""
        return self._content.year

    @property
    def aliases(self) -> tuple[Alias, ...]:
        """Все алиасы (импортные и ручные)."""
        return self._content.aliases

    @property
    def persons(self) -> tuple[PersonCredit, ...]:
        """Персоны по порядку."""
        return self._content.persons

    @property
    def genres(self) -> tuple[str, ...]:
        """Жанры (slug)."""
        return self._content.genres

    @property
    def external_refs(self) -> dict[ExternalSource, str]:
        """Внешние идентификаторы."""
        return dict(self._content.external_refs)

    @property
    def metrics(self) -> MediaMetrics:
        """Метрики."""
        return self._content.metrics

    @property
    def source_media(self) -> tuple[SourceMedia, ...]:
        """Исходники для загадок."""
        return self._content.source_media

    @property
    def countries(self) -> tuple[str, ...]:
        """Страны производства (ISO 3166-1 alpha-2)."""
        return self._content.countries

    @property
    def clue_texts(self) -> ApprovedClueTexts:
        """Одобренные тексты загадок (то, что уходит в события)."""
        return self._content.clue_texts

    @property
    def is_active(self) -> bool:
        """Можно ли показывать единицу в выдаче."""
        return self.status is ItemStatus.ACTIVE

    @property
    def subtitle(self) -> str | None:
        """Подпись в автокомплите: артисты трека или оригинальное название фильма."""
        if self.kind is MediaKind.TRACK:
            artists = [p.name for p in self.persons if p.role is PersonRole.ARTIST]
            return ", ".join(artists)[:MAX_TITLE_LEN] or None
        if self.original_title and self.original_title != self.title:
            return self.original_title
        directors = [p.name for p in self.persons if p.role is PersonRole.DIRECTOR]
        return ", ".join(directors)[:MAX_TITLE_LEN] or None

    # ------------------------------------------------------------------ поведение

    def apply_import(
        self, data: ItemData, normalizer: TitleNormalizer, *, current_year: int
    ) -> tuple[str, ...]:
        """Слить свежие метаданные импорта с текущими.

        Импортные алиасы заменяются новыми, ручные сохраняются (при совпадении
        нормализованной формы побеждает ручной).

        Returns:
            Изменённые поля; пустой кортеж — ничего не поменялось (события нет).
        """
        if data.kind is not self.kind:
            raise ConflictError(
                "вид единицы не совпадает с импортом", code="catalog.import_kind_mismatch"
            )
        manual = tuple(a for a in self.aliases if a.source is AliasSource.MANUAL)
        content = _build_content(
            self.id, data, normalizer, current_year, keep=manual, keep_texts=self.clue_texts
        )
        return self._change(content)

    def set_clue_texts(self, approved: ApprovedClueTexts) -> bool:
        """Опубликовать одобренные тексты загадок (ADR 0024).

        Returns:
            ``True`` — снимок изменился (``MediaItemUpdated`` с ``clue_texts``).
        """
        content = _Content(**{**_as_dict(self._content), "clue_texts": approved})
        return bool(self._change(content))

    def republish(self) -> None:
        """Выпустить снимок заново: ``version+1`` и ``MediaItemUpdated`` без изменений данных.

        Нужно, чтобы потребители заполнили новые поля для старых единиц (план этапа 3a, D9):
        их проекции отбрасывают события с ``version`` не больше применённой.
        """
        self.version += 1
        self.record(
            MediaItemUpdated(
                aggregate_id=str(self.id),
                snapshot=self.snapshot(),
                changed_fields=(REPUBLISHED_FIELD,),
            )
        )

    def replace_aliases(self, drafts: Iterable[AliasDraft], normalizer: TitleNormalizer) -> bool:
        """Заменить алиасы списком модератора (все становятся ручными).

        Returns:
            ``True``, если набор алиасов изменился.

        Raises:
            DomainError: нет ни одного алиаса вида ``title`` или их слишком много.
        """
        aliases = _materialize_aliases(drafts, normalizer, AliasSource.MANUAL)
        if not any(a.kind is AliasKind.TITLE for a in aliases):
            raise DomainError(
                "нужен хотя бы один алиас вида title", code="catalog.title_alias_required"
            )
        content = _Content(**{**_as_dict(self._content), "aliases": aliases})
        return bool(self._change(content))

    def block(self, reason_code: str, actor_id: uuid.UUID | None) -> None:
        """Заблокировать единицу.

        Raises:
            ConflictError: единица уже заблокирована.
            DomainError: недопустимый ``reason_code``.
        """
        if len(reason_code) > MAX_REASON_CODE_LEN or not REASON_CODE_RE.match(reason_code):
            raise DomainError("недопустимый reason_code", code="catalog.invalid_reason_code")
        if self.status is ItemStatus.BLOCKED:
            raise ConflictError("единица уже заблокирована", code="catalog.item_already_blocked")
        self.status = ItemStatus.BLOCKED
        self.blocked_reason_code = reason_code
        self.version += 1
        self.record(
            MediaItemBlocked(
                aggregate_id=str(self.id),
                media_item_id=str(self.id),
                version=self.version,
                reason_code=reason_code,
                actor_id=str(actor_id) if actor_id else None,
            )
        )

    def unblock(self, actor_id: uuid.UUID | None) -> None:
        """Снять блокировку.

        Raises:
            ConflictError: единица не заблокирована.
        """
        if self.status is not ItemStatus.BLOCKED:
            raise ConflictError("единица не заблокирована", code="catalog.item_not_blocked")
        self.status = ItemStatus.ACTIVE
        self.blocked_reason_code = None
        self.version += 1
        self.record(
            MediaItemUnblocked(
                aggregate_id=str(self.id),
                media_item_id=str(self.id),
                version=self.version,
                actor_id=str(actor_id) if actor_id else None,
            )
        )

    def snapshot(self) -> dict[str, Any]:
        """Полный снимок в форме контракта ``MediaItemCreated``/``Updated``.

        ``countries`` и ``clue_texts`` (только одобренные) — необязательные поля
        контракта: пустые не публикуются.
        """
        c = self._content
        snapshot = {
            "media_item_id": str(self.id),
            "version": self.version,
            "domain": self.domain.value,
            "kind": self.kind.value,
            "title": c.title,
            "original_title": c.original_title,
            "year": c.year,
            "status": self.status.value,
            "aliases": [a.to_contract() for a in c.aliases],
            "persons": [p.to_contract() for p in c.persons],
            "genres": list(c.genres),
            "external_refs": {
                src.value: ref for src, ref in c.external_refs if src in PUBLISHED_EXTERNAL_SOURCES
            },
            "metrics": c.metrics.to_contract(),
            "source_media": [s.to_contract() for s in c.source_media],
        }
        if c.countries:
            snapshot["countries"] = list(c.countries)
        if not c.clue_texts.is_empty:
            snapshot["clue_texts"] = c.clue_texts.to_contract()
        return snapshot

    # ------------------------------------------------------------------ внутреннее

    def _change(self, content: _Content) -> tuple[str, ...]:
        before, after = _as_dict(self._content), _as_dict(content)
        changed = tuple(f for f in _TRACKED_FIELDS if before[f] != after[f])
        if not changed:
            return ()
        self._content = content
        self.version += 1
        self.record(
            MediaItemUpdated(
                aggregate_id=str(self.id), snapshot=self.snapshot(), changed_fields=changed
            )
        )
        return changed


def _as_dict(content: _Content) -> dict[str, Any]:
    return {name: getattr(content, name) for name in content.__dataclass_fields__}


def _person_key(person: PersonCredit) -> tuple[str, int, str]:
    return (person.role.value, person.order, str(person.person_id))


def _build_content(
    item_id: uuid.UUID,
    data: ItemData,
    normalizer: TitleNormalizer,
    current_year: int,
    *,
    keep: tuple[Alias, ...],
    keep_texts: ApprovedClueTexts,
) -> _Content:
    title = validate_title(data.title)
    original = data.original_title.strip() if data.original_title else None
    original = original[:MAX_TITLE_LEN] if original else None
    validate_year(data.year, current_year)

    drafts = [AliasDraft(title, AliasKind.TITLE), *data.aliases]
    if original and original != title:
        drafts.insert(1, AliasDraft(original, AliasKind.ALT))
    imported = _materialize_aliases(drafts, normalizer, AliasSource.IMPORT)
    kept_norms = {a.normalized for a in keep}
    aliases = tuple(a for a in imported if a.normalized not in kept_norms) + keep

    genres: list[str] = []
    for raw in data.genres:
        genre = normalize_genre(raw)
        if genre and genre not in genres:
            genres.append(genre)

    sources: list[SourceMedia] = []
    for draft in data.source_media:
        media = draft.materialize(item_id)
        if media not in sources:
            sources.append(media)

    return _Content(
        title=title,
        original_title=original,
        year=data.year,
        aliases=aliases,
        persons=tuple(sorted(dict.fromkeys(data.persons), key=_person_key)),
        genres=tuple(genres),
        external_refs=tuple(sorted(data.external_refs.items())),
        metrics=data.metrics,
        source_media=tuple(sources),
        countries=normalize_countries(data.countries),
        clue_texts=data.clue_texts if data.clue_texts is not None else keep_texts,
    )


def _materialize_aliases(
    drafts: Iterable[AliasDraft], normalizer: TitleNormalizer, source: AliasSource
) -> tuple[Alias, ...]:
    """Нормализовать, удалить дубли (по normalized) и добавить транслит-варианты."""
    result: dict[str, Alias] = {}
    translits: list[Alias] = []
    for draft in drafts:
        text = draft.text.strip()[:MAX_TITLE_LEN]
        normalized = normalizer.normalize(text)[:MAX_TITLE_LEN]
        if not normalized:
            continue
        existing = result.get(normalized)
        if existing is not None:
            if existing.lang is None and draft.lang:
                result[normalized] = Alias(
                    existing.text, normalized, draft.lang, existing.kind, source
                )
            continue
        result[normalized] = Alias(text, normalized, draft.lang, draft.kind, source)
        if draft.kind is not AliasKind.TRANSLIT:
            variant = normalizer.translit(normalized)
            if variant:
                variant = variant[:MAX_TITLE_LEN]
                translits.append(Alias(variant, variant, None, AliasKind.TRANSLIT, source))
    for alias in translits:
        result.setdefault(alias.normalized, alias)
    if len(result) > MAX_ALIASES:
        raise DomainError("слишком много алиасов", code="catalog.too_many_aliases")
    return tuple(result.values())
