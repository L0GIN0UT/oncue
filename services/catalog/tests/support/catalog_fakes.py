"""Фейки портов catalog для unit-тестов use-cases.

Общие фейки (часы, UoW, очередь задач) — из ``gv_testkit``; здесь только хранилища и шлюзы
этого сервиса, которых testkit знать не может.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from catalog_service.application.dto import (
    AdminItemFilter,
    ClueStatus,
    ImportedItem,
    ItemSummary,
    RecordedAction,
    SearchDocument,
    SearchQuery,
    Suggestion,
)
from catalog_service.domain.clue_texts import ClueTexts
from catalog_service.domain.import_job import ImportCounters, ImportJob
from catalog_service.domain.media_item import MediaItem
from catalog_service.domain.model import ExternalSource, MediaDomain
from catalog_service.domain.republish import RepublishJob

from gv_core.errors import ConflictError, DependencyUnavailableError, NotFoundError
from gv_core.pagination import Page
from gv_testkit import FakeUnitOfWork, InMemoryIdempotencyStore


def clone_item(item: MediaItem) -> MediaItem:
    """Копия агрегата «как из БД» (без событий)."""
    return MediaItem.restore(
        item.id,
        kind=item.kind,
        status=item.status,
        version=item.version,
        title=item.title,
        original_title=item.original_title,
        year=item.year,
        aliases=item.aliases,
        persons=item.persons,
        genres=item.genres,
        external_refs=item.external_refs,
        metrics=item.metrics,
        source_media=item.source_media,
        blocked_reason_code=item.blocked_reason_code,
        countries=item.countries,
        clue_texts=item.clue_texts,
    )


def clone_job(job: ImportJob) -> ImportJob:
    return ImportJob(
        job.id,
        source=job.source,
        ref=job.ref,
        file_id=job.file_id,
        actor_id=job.actor_id,
        reason=job.reason,
        idempotency_key=job.idempotency_key,
        status=job.status,
        counters=ImportCounters(**job.counters.as_dict()),
        error=job.error,
    )


@dataclass
class CatalogStore:
    """Общее «хранилище» для всех UoW одного теста."""

    items: dict[uuid.UUID, MediaItem] = field(default_factory=dict)
    persons: dict[str, uuid.UUID] = field(default_factory=dict)
    jobs: dict[uuid.UUID, ImportJob] = field(default_factory=dict)
    actions: dict[tuple[uuid.UUID, str], RecordedAction] = field(default_factory=dict)
    clue: dict[tuple[uuid.UUID, str, str], ClueStatus] = field(default_factory=dict)
    texts: dict[uuid.UUID, ClueTexts] = field(default_factory=dict)
    republish: dict[uuid.UUID, RepublishJob] = field(default_factory=dict)
    idempotency: InMemoryIdempotencyStore = field(default_factory=InMemoryIdempotencyStore)
    events: list[object] = field(default_factory=list)


class InMemoryItems:
    def __init__(self, store: CatalogStore) -> None:
        self._s = store

    async def get(self, item_id: uuid.UUID) -> MediaItem | None:
        item = self._s.items.get(item_id)
        return clone_item(item) if item else None

    async def get_many(self, item_ids: Sequence[uuid.UUID]) -> list[MediaItem]:
        return [clone_item(self._s.items[i]) for i in item_ids if i in self._s.items]

    async def find_by_external_ref(
        self, source: ExternalSource, external_id: str
    ) -> MediaItem | None:
        for item in self._s.items.values():
            if item.external_refs.get(source) == external_id:
                return clone_item(item)
        return None

    async def exists(self, item_id: uuid.UUID) -> bool:
        return item_id in self._s.items

    async def add(self, item: MediaItem) -> None:
        self._s.items[item.id] = clone_item(item)
        item.persisted_version = item.version

    async def save(self, item: MediaItem) -> None:
        stored = self._s.items[item.id]
        if stored.version != item.persisted_version:
            raise ConflictError("версия изменилась", code="catalog.concurrent_update")
        self._s.items[item.id] = clone_item(item)
        item.persisted_version = item.version

    async def list_page(
        self, flt: AdminItemFilter, cursor: str | None, limit: int
    ) -> Page[ItemSummary]:
        rows = [
            ItemSummary(i.id, i.domain, i.kind, i.title, i.year, i.status.value, i.version)
            for i in sorted(self._s.items.values(), key=lambda i: i.id, reverse=True)
            if (flt.domain is None or i.domain is flt.domain)
            and (flt.status is None or i.status.value == flt.status)
            and (
                flt.normalized_q is None
                or any(a.normalized.startswith(flt.normalized_q) for a in i.aliases)
            )
        ]
        return Page(rows[:limit], None)

    async def list_ids_after(
        self, domain: MediaDomain | None, after: uuid.UUID | None, limit: int
    ) -> list[uuid.UUID]:
        ids = sorted(
            i.id
            for i in self._s.items.values()
            if (domain is None or i.domain is domain) and (after is None or i.id > after)
        )
        return ids[:limit]


class InMemoryPersons:
    def __init__(self, store: CatalogStore) -> None:
        self._s = store

    async def resolve(self, key: str, name: str) -> uuid.UUID:
        return self._s.persons.setdefault(key, uuid.uuid5(uuid.NAMESPACE_URL, key))


class InMemoryJobs:
    def __init__(self, store: CatalogStore) -> None:
        self._s = store

    async def get(self, job_id: uuid.UUID) -> ImportJob | None:
        job = self._s.jobs.get(job_id)
        return clone_job(job) if job else None

    async def find_by_key(self, actor_id: uuid.UUID, idempotency_key: str) -> ImportJob | None:
        for job in self._s.jobs.values():
            if job.actor_id == actor_id and job.idempotency_key == idempotency_key:
                return clone_job(job)
        return None

    async def add(self, job: ImportJob) -> None:
        self._s.jobs[job.id] = clone_job(job)

    async def save(self, job: ImportJob) -> None:
        self._s.jobs[job.id] = clone_job(job)


class InMemoryActions:
    def __init__(self, store: CatalogStore) -> None:
        self._s = store

    async def record(self, action: RecordedAction) -> None:
        self._s.actions[(action.actor_id, action.idempotency_key)] = action


class InMemoryClueStatus:
    def __init__(self, store: CatalogStore) -> None:
        self._s = store

    async def get(
        self, media_item_id: uuid.UUID, clue_type: str, profile_key: str
    ) -> ClueStatus | None:
        return self._s.clue.get((media_item_id, clue_type, profile_key))

    async def upsert(self, status: ClueStatus) -> None:
        self._s.clue[(status.media_item_id, status.clue_type, status.profile_key)] = status


class InMemoryClueTexts:
    def __init__(self, store: CatalogStore) -> None:
        self._s = store

    async def get(self, media_item_id: uuid.UUID) -> ClueTexts | None:
        texts = self._s.texts.get(media_item_id)
        return copy.copy(texts) if texts else None

    async def save(self, texts: ClueTexts) -> None:
        if texts.media_item_id not in self._s.items:
            raise AssertionError("тексты без единицы (в БД это FK)")
        texts.persisted = True
        self._s.texts[texts.media_item_id] = copy.copy(texts)


class InMemoryRepublish:
    def __init__(self, store: CatalogStore) -> None:
        self._s = store

    async def add(self, job: RepublishJob) -> None:
        self._s.republish[job.id] = copy.copy(job)

    async def get(self, job_id: uuid.UUID) -> RepublishJob | None:
        job = self._s.republish.get(job_id)
        return copy.copy(job) if job else None

    async def save(self, job: RepublishJob) -> None:
        self._s.republish[job.id] = copy.copy(job)


class FakeCatalogUow(FakeUnitOfWork):
    """UoW из testkit + репозитории в памяти; события копятся в ``store.events``."""

    def __init__(self, store: CatalogStore) -> None:
        super().__init__()
        self.store = store
        self.items = InMemoryItems(store)
        self.persons = InMemoryPersons(store)
        self.imports = InMemoryJobs(store)
        self.actions = InMemoryActions(store)
        self.idempotency = store.idempotency
        self.clue_status = InMemoryClueStatus(store)
        self.clue_texts = InMemoryClueTexts(store)
        self.republish = InMemoryRepublish(store)

    async def commit(self) -> None:
        await super().commit()
        self.store.events.extend(self.events)
        self.events = []


@dataclass
class FakeCache:
    data: dict[str, list[Suggestion]] = field(default_factory=dict)
    invalidations: int = 0

    async def get(self, key: str) -> list[Suggestion] | None:
        return self.data.get(key)

    async def set(self, key: str, value: list[Suggestion]) -> None:
        self.data[key] = value

    async def invalidate(self) -> None:
        self.invalidations += 1
        self.data.clear()


def _name_words(names: tuple[str, ...]) -> list[str]:
    """Имя целиком и каждое слово с продолжением: «нолан» находит «Кристофер Нолан»."""
    out: list[str] = []
    for name in names:
        words = name.casefold().split()
        out.extend(" ".join(words[i:]) for i in range(len(words)))
    return out


@dataclass
class FakeSearch:
    """Поисковый бэкенд в памяти; ``down=True`` — недоступен."""

    docs: dict[uuid.UUID, SearchDocument] = field(default_factory=dict)
    down: bool = False
    calls: int = 0

    async def autocomplete(self, query: SearchQuery) -> list[Suggestion]:
        return self._match(query, prefix=True)

    async def search(self, query: SearchQuery) -> list[Suggestion]:
        return self._match(query, prefix=False)

    async def index(self, doc: SearchDocument) -> bool:
        current = self.docs.get(doc.id)
        if current is not None and current.version >= doc.version:
            return False
        self.docs[doc.id] = doc
        return True

    def _match(self, query: SearchQuery, *, prefix: bool) -> list[Suggestion]:
        self.calls += 1
        if self.down:
            raise DependencyUnavailableError("down")
        scope = query.scope_person
        # Как в боевых бэкендах: имя автора ищется только вне области персоны.
        hits = [
            d
            for d in self.docs.values()
            if d.status == "active"
            and (query.domain is None or d.domain is query.domain)
            and (scope is None or str(scope) in d.person_ids)
            and any(
                a.startswith(query.normalized) if prefix else query.normalized in a
                for a in (*d.aliases, *(() if scope else _name_words(d.people)))
            )
        ]
        return [
            Suggestion(d.id, d.domain, d.title, d.year, d.subtitle, d.creator)
            for d in hits[: query.limit]
        ]


@dataclass
class FakeFilmProvider:
    films: dict[str, ImportedItem] = field(default_factory=dict)

    async def fetch_film(self, ref: str) -> ImportedItem:
        if ref not in self.films:
            raise NotFoundError("нет", code="catalog.external_not_found")
        return self.films[ref]


@dataclass
class FakeMusicProvider:
    tracks: dict[str, ImportedItem] = field(default_factory=dict)
    down: bool = False

    async def get_track(self, ref: str) -> ImportedItem:
        if self.down:
            raise DependencyUnavailableError("down", code="catalog.source_unavailable")
        return self.tracks[ref]

    async def search_tracks(self, query: str, limit: int) -> list[ImportedItem]:
        return [t for t in self.tracks.values() if query.casefold() in t.title.casefold()][:limit]


@dataclass
class FakeFileReader:
    files: dict[str, list[ImportedItem | ValueError]] = field(default_factory=dict)

    async def read(self, file_id: str) -> AsyncIterator[ImportedItem | ValueError]:
        if file_id not in self.files:
            raise NotFoundError("нет файла", code="catalog.import_file_not_found")
        for entry in self.files[file_id]:
            yield entry
