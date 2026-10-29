"""Фикстуры тестов media_pipeline.

Инфраструктура и фейки общего назначения (часы, очередь задач, токены, клиент API) —
из ``gv_testkit``. Здесь только фейки портов самого сервиса (testkit не знает его домена):
репозитории в памяти с транзакционной семантикой, хранилище, обработка медиа, подпись
ссылок. Тесты получают их через фикстуру ``media`` (без импорта модуля и правки sys.path).
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self
from uuid import UUID

import pytest
from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.ports import (
    ANY_SCOPE,
    AdminAction,
    EncodedImage,
    FetchedSource,
    ImageOps,
    NormalizedMedia,
    ObjectPage,
    ProfileSetCount,
    RenderedImage,
    ScopeFilter,
    StorageArea,
    StoredObject,
)
from media_pipeline_service.domain.asset_set import AssetSet
from media_pipeline_service.domain.collections import CollectionState
from media_pipeline_service.domain.dynamic_profiles import DynamicProfile, DynamicProfileStatus
from media_pipeline_service.domain.errors import InvalidUgcImageError
from media_pipeline_service.domain.media_items import ItemStatus, MediaItemState
from media_pipeline_service.domain.packages import ImportPackage
from media_pipeline_service.domain.sources import AssetScope, SourceKind, SourceMedia
from media_pipeline_service.domain.ugc import UgcImage, UgcPurpose, UgcStatus

from gv_core.domain import AggregateRoot, DomainEvent
from gv_core.errors import ConflictError
from gv_db.idempotency import IdempotencyRecord

if TYPE_CHECKING:
    from media_pipeline_service.application.commands.collections import SyncCollectionAssets
    from media_pipeline_service.application.queries.sources import ListSources


@dataclass
class MemoryDb:
    """«База» в памяти, общая для всех UoW теста."""

    asset_sets: dict[UUID, AssetSet] = field(default_factory=dict)
    sources: dict[UUID, SourceMedia] = field(default_factory=dict)
    items: dict[UUID, MediaItemState] = field(default_factory=dict)
    # Счётчик версий событий по единице (колонка media_items_state.events_version).
    event_versions: dict[UUID, int] = field(default_factory=dict)
    links: list[dict[str, Any]] = field(default_factory=list)
    idempotency: dict[tuple[UUID, str], IdempotencyRecord] = field(default_factory=dict)
    audit: list[AdminAction] = field(default_factory=list)
    events: list[DomainEvent] = field(default_factory=list)
    profiles: dict[UUID, DynamicProfile] = field(default_factory=dict)
    collections: dict[UUID, CollectionState] = field(default_factory=dict)
    ugc: dict[UUID, UgcImage] = field(default_factory=dict)
    packages: dict[UUID, ImportPackage] = field(default_factory=dict)
    purge: dict[str, datetime] = field(default_factory=dict)


class _Repo:
    def __init__(self, uow: FakeMediaUnitOfWork) -> None:
        self._uow = uow

    @property
    def db(self) -> MemoryDb:
        return self._uow.view


class FakeAssetSets(_Repo):
    async def get(self, asset_set_id: UUID, *, for_update: bool = False) -> AssetSet | None:
        found = self.db.asset_sets.get(asset_set_id)
        return copy.deepcopy(found) if found else None

    async def list_for_item(
        self,
        media_item_id: UUID,
        clue_type: str | None = None,
        *,
        for_update: bool = False,
        scope: ScopeFilter = ANY_SCOPE,
    ) -> list[AssetSet]:
        return [
            copy.deepcopy(s)
            for s in sorted(self.db.asset_sets.values(), key=lambda a: a.created_at)
            if s.media_item_id == media_item_id
            and clue_type in (None, s.clue_type)
            and (scope is ANY_SCOPE or s.scope == scope)
        ]

    async def list_for_scope(
        self, scope: AssetScope, *, for_update: bool = False
    ) -> list[AssetSet]:
        return [
            copy.deepcopy(s)
            for s in sorted(self.db.asset_sets.values(), key=lambda a: (a.media_item_id, a.id))
            if s.scope == scope
        ]

    async def delete(self, asset_set_ids: Sequence[UUID]) -> None:
        for asset_set_id in asset_set_ids:
            self.db.asset_sets.pop(asset_set_id, None)

    async def add(self, aggregate: AssetSet) -> None:
        if any(
            (s.media_item_id, s.clue_type, s.profile_key, s.params_hash, s.scope)
            == (
                aggregate.media_item_id,
                aggregate.clue_type,
                aggregate.profile_key,
                aggregate.params_hash,
                aggregate.scope,
            )
            for s in self.db.asset_sets.values()
        ):
            raise ConflictError("дубль набора")
        stored = copy.deepcopy(aggregate)
        stored.persisted_version = stored.version
        stored.pull_events()
        self.db.asset_sets[aggregate.id] = stored
        aggregate.persisted_version = aggregate.version

    async def save(self, aggregate: AssetSet) -> None:
        current = self.db.asset_sets.get(aggregate.id)
        if current is None or current.version != aggregate.persisted_version:
            raise ConflictError("версия набора изменилась")
        stored = copy.deepcopy(aggregate)
        stored.persisted_version = stored.version
        stored.pull_events()
        self.db.asset_sets[aggregate.id] = stored
        aggregate.persisted_version = aggregate.version

    async def count_by_profile(
        self, clue_type: str | None = None, params_hash: str | None = None
    ) -> list[ProfileSetCount]:
        counts: dict[tuple[str, str, str, str], int] = {}
        for s in self.db.asset_sets.values():
            if clue_type not in (None, s.clue_type) or params_hash not in (None, s.params_hash):
                continue
            key = (s.clue_type, s.profile_key, s.params_hash, s.status.value)
            counts[key] = counts.get(key, 0) + 1
        return [ProfileSetCount(*key, count=n) for key, n in counts.items()]


class FakeSources(_Repo):
    async def get(self, source_id: UUID) -> SourceMedia | None:
        found = self.db.sources.get(source_id)
        return copy.deepcopy(found) if found else None

    async def list_for_item(
        self, media_item_id: UUID, *, scope: AssetScope | None = None
    ) -> list[SourceMedia]:
        return [
            copy.deepcopy(s)
            for s in self.db.sources.values()
            if s.media_item_id == media_item_id and s.scope == scope
        ]

    async def list_unattributed(self, *, limit: int, after_id: UUID | None) -> list[SourceMedia]:
        found = sorted(
            (
                s
                for s in self.db.sources.values()
                if s.scope is None
                and s.kind is not SourceKind.TEXT
                and (not s.source_url or not s.license_note)
                and (after_id is None or s.id > after_id)
            ),
            key=lambda s: s.id,
        )
        return [copy.deepcopy(s) for s in found[:limit]]

    async def list_for_scope(self, scope: AssetScope) -> list[SourceMedia]:
        return [copy.deepcopy(s) for s in self.db.sources.values() if s.scope == scope]

    async def scopes_for_item(self, media_item_id: UUID) -> list[AssetScope]:
        scopes = {
            s.scope
            for s in self.db.sources.values()
            if s.media_item_id == media_item_id and s.scope is not None
        }
        return sorted(scopes, key=lambda sc: (sc.kind.value, sc.id))

    async def add(self, source: SourceMedia) -> None:
        if source.media_item_id not in self.db.items:
            raise AssertionError("исходник без проекции единицы (в БД это FK)")
        if source.scope is not None and any(
            (s.scope, s.media_item_id) == (source.scope, source.media_item_id) and s.id != source.id
            for s in self.db.sources.values()
        ):
            raise ConflictError("второй исходник владельца у единицы (uq_source_media_scope_item)")
        self.db.sources.setdefault(source.id, copy.deepcopy(source))

    async def save(self, source: SourceMedia) -> None:
        self.db.sources[source.id] = copy.deepcopy(source)

    async def delete(self, source_ids: Sequence[UUID]) -> None:
        for source_id in source_ids:
            self.db.sources.pop(source_id, None)


class FakeItems(_Repo):
    async def get(self, media_item_id: UUID, *, for_update: bool = False) -> MediaItemState | None:
        found = self.db.items.get(media_item_id)
        return copy.deepcopy(found) if found else None

    async def get_many(
        self, media_item_ids: Sequence[UUID], *, for_update: bool = False
    ) -> dict[UUID, MediaItemState]:
        return {
            i: copy.deepcopy(self.db.items[i]) for i in sorted(media_item_ids) if i in self.db.items
        }

    async def upsert(self, state: MediaItemState) -> None:
        self.db.items[state.id] = copy.deepcopy(state)

    async def list_active_ids(self, domain: str, *, after: UUID | None, limit: int) -> list[UUID]:
        ids = sorted(
            i.id
            for i in self.db.items.values()
            if i.domain == domain
            and i.status is ItemStatus.ACTIVE
            and (after is None or i.id > after)
        )
        return ids[:limit]

    async def next_event_version(self, media_item_id: UUID) -> int:
        if media_item_id not in self.db.items:
            raise AssertionError("набор без проекции единицы (в БД это FK)")
        value = self.db.event_versions.get(media_item_id, 0) + 1
        self.db.event_versions[media_item_id] = value
        return value


class FakeLinks(_Repo):
    async def record(
        self,
        *,
        asset_set_id: UUID,
        level: int,
        subject_hash: str,
        issued_at: datetime,
        expires_at: datetime,
        client_id: str | None = None,
    ) -> None:
        self.db.links.append(
            {
                "asset_set_id": asset_set_id,
                "level": level,
                "subject_hash": subject_hash,
                "issued_at": issued_at,
                "expires_at": expires_at,
                "client_id": client_id,
            }
        )

    async def purge_older_than(self, cutoff: datetime, *, limit: int) -> int:
        before = len(self.db.links)
        self.db.links[:] = [r for r in self.db.links if r["issued_at"] >= cutoff]
        return before - len(self.db.links)


class FakeProfiles(_Repo):
    async def lock(self) -> None:
        return None

    async def get(
        self, clue_type: str, params_hash: str, domain: str, *, for_update: bool = False
    ) -> DynamicProfile | None:
        for p in self.db.profiles.values():
            if (p.clue_type, p.params_hash, p.domain) == (clue_type, params_hash, domain):
                return copy.deepcopy(p)
        return None

    async def count_usable(self) -> int:
        return sum(1 for p in self.db.profiles.values() if p.is_usable)

    async def add(self, profile: DynamicProfile) -> None:
        if await self.get(profile.clue_type, profile.params_hash, profile.domain):
            raise ConflictError("дубль профиля")
        self.db.profiles[profile.id] = copy.deepcopy(profile)

    async def save(self, profile: DynamicProfile) -> None:
        self.db.profiles[profile.id] = copy.deepcopy(profile)

    def _sorted(self) -> list[DynamicProfile]:
        return [
            copy.deepcopy(p) for p in sorted(self.db.profiles.values(), key=lambda p: p.created_at)
        ]

    async def list_usable(self, domain: str) -> list[DynamicProfile]:
        return [p for p in self._sorted() if p.domain == domain and p.is_usable]

    async def list_backfilling(self, limit: int) -> list[DynamicProfile]:
        return [p for p in self._sorted() if p.status is DynamicProfileStatus.BACKFILLING][:limit]

    async def list_all(
        self, clue_type: str | None = None, params_hash: str | None = None
    ) -> list[DynamicProfile]:
        return [
            p
            for p in self._sorted()
            if clue_type in (None, p.clue_type) and params_hash in (None, p.params_hash)
        ]


class FakeCollections(_Repo):
    async def advance(self, state: CollectionState) -> bool:
        current = self.db.collections.get(state.id)
        if current is not None and current.version >= state.version:
            return False
        self.db.collections[state.id] = copy.deepcopy(state)
        return True


class FakeIdempotency(_Repo):
    """``gv_db.idempotency.IdempotencyStore`` в транзакции фейкового UoW.

    В отличие от ``gv_testkit.InMemoryIdempotencyStore`` ключ живёт в «снимке» UoW: откат
    транзакции мутации откатывает и ключ — как в PostgreSQL.
    """

    async def find(self, actor_id: UUID, key: str) -> IdempotencyRecord | None:
        return self.db.idempotency.get((actor_id, key))

    async def save(
        self,
        actor_id: UUID,
        key: str,
        request_hash: str,
        response: dict[str, Any],
        *,
        status_code: int = 200,
    ) -> None:
        if (actor_id, key) in self.db.idempotency:
            raise ConflictError(
                "запрос с этим Idempotency-Key уже выполняется", code="idempotency.in_progress"
            )
        self.db.idempotency[actor_id, key] = IdempotencyRecord(
            actor_id=actor_id,
            key=key,
            request_hash=request_hash,
            response=response,
            status_code=status_code,
            created_at=self._uow.clock_now,
        )


class FakePackages(_Repo):
    async def get(self, package_id: UUID, *, for_update: bool = False) -> ImportPackage | None:
        found = self.db.packages.get(package_id)
        return copy.deepcopy(found) if found else None

    async def active_archive_keys(self, keys: Sequence[str]) -> set[str]:
        wanted = set(keys)
        return {
            p.archive_key
            for p in self.db.packages.values()
            if p.archive_key is not None and p.archive_key in wanted
        }

    async def add(self, package: ImportPackage) -> None:
        self.db.packages[package.id] = copy.deepcopy(package)

    async def save(self, package: ImportPackage) -> None:
        stored = copy.deepcopy(package)
        stored.new_errors = []
        stored.errors_reset = False
        self.db.packages[package.id] = stored
        package.new_errors = []
        package.errors_reset = False


class FakeUgc(_Repo):
    async def add(self, image: UgcImage) -> None:
        self.db.ugc[image.id] = image

    async def get(self, image_id: UUID, *, for_update: bool = False) -> UgcImage | None:
        return self.db.ugc.get(image_id)

    async def save(self, image: UgcImage) -> None:
        self.db.ugc[image.id] = image

    async def list_approved_for_owner(
        self, owner_user_id: UUID, purpose: str, *, exclude: UUID
    ) -> list[UgcImage]:
        return [
            i
            for i in self.db.ugc.values()
            if i.owner_user_id == owner_user_id
            and i.purpose == purpose
            and i.status is UgcStatus.APPROVED
            and i.id != exclude
        ]

    async def pending_keys(self, keys: Sequence[str]) -> set[str]:
        return {
            i.pending_key
            for i in self.db.ugc.values()
            if i.status is UgcStatus.PENDING and i.pending_key in keys
        }  # type: ignore[misc]  # pending_key у pending не None

    async def enqueue_purge(self, keys: Sequence[str], now: datetime) -> None:
        for key in keys:
            self.db.purge.setdefault(key, now)

    async def claim_purge(self, limit: int) -> list[str]:
        return sorted(self.db.purge, key=lambda k: (self.db.purge[k], k))[:limit]

    async def forget_purge(self, keys: Sequence[str]) -> None:
        for key in keys:
            self.db.purge.pop(key, None)

    async def lock_owner(self, owner_user_id: UUID, purpose: str) -> None:
        return None

    async def list_pending_for_owner(self, owner_user_id: UUID, purpose: str) -> list[UgcImage]:
        return [
            i
            for i in self.db.ugc.values()
            if i.owner_user_id == owner_user_id
            and i.purpose == purpose
            and i.status is UgcStatus.PENDING
        ]

    async def queue(
        self,
        status: UgcStatus,
        purpose: str | None,
        *,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[UgcImage]:
        rows = [
            i
            for i in self.db.ugc.values()
            if i.status is status and (purpose is None or i.purpose == purpose)
        ]
        rows.sort(key=lambda i: (i.submitted_at, i.id))
        if after is not None:
            rows = [i for i in rows if (i.submitted_at, i.id) > after]
        return rows[:limit]


class FakeAudit(_Repo):
    async def record(self, action: AdminAction) -> None:
        self.db.audit.append(action)


class FakeMediaUnitOfWork:
    """UoW в памяти: изменения видны другим UoW только после ``commit``.

    Хуки для тестов сбоев (получают «базу», могут бросить исключение):
    ``fail_commit`` — перед фиксацией (коммит не случится, как при обрыве соединения),
    ``after_commit`` — сразу после фиксации (процесс «упал» после коммита).
    """

    def __init__(
        self,
        db: MemoryDb,
        clock_now: datetime,
        fail_commit: Callable[[MemoryDb], None] | None = None,
        after_commit: Callable[[MemoryDb], None] | None = None,
    ) -> None:
        self._db = db
        self.view = db
        self.clock_now = clock_now
        self._fail_commit = fail_commit
        self._after_commit = after_commit
        self._tracked: list[AggregateRoot[Any]] = []
        self.committed = False
        self.asset_sets = FakeAssetSets(self)
        self.sources = FakeSources(self)
        self.items = FakeItems(self)
        self.links = FakeLinks(self)
        self.profiles = FakeProfiles(self)
        self.collections = FakeCollections(self)
        self.ugc = FakeUgc(self)
        self.packages = FakePackages(self)
        self.idempotency = FakeIdempotency(self)
        self.audit = FakeAudit(self)

    async def __aenter__(self) -> Self:
        self.view = copy.deepcopy(self._db)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self.committed:
            await self.rollback()

    def track(self, aggregate: AggregateRoot[Any]) -> None:
        if aggregate not in self._tracked:
            self._tracked.append(aggregate)

    async def commit(self) -> None:
        if self._fail_commit is not None:
            self._fail_commit(self.view)
        for aggregate in self._tracked:
            self.view.events.extend(aggregate.pull_events())
        for name in (
            "asset_sets",
            "sources",
            "items",
            "event_versions",
            "idempotency",
            "profiles",
            "collections",
            "ugc",
            "packages",
            "purge",
        ):
            setattr(self._db, name, getattr(self.view, name))
        self._db.links[:] = self.view.links
        self._db.audit[:] = self.view.audit
        self._db.events[:] = self.view.events
        self.committed = True
        if self._after_commit is not None:
            self._after_commit(self._db)

    async def rollback(self) -> None:
        for aggregate in self._tracked:
            aggregate.pull_events()
        self.view = self._db


@dataclass
class FakeStorage:
    """Хранилище объектов в памяти."""

    objects: dict[tuple[StorageArea, str], tuple[bytes, str]] = field(default_factory=dict)
    fail_put: bool = False
    fail_copy: bool = False
    fail_delete: bool = False
    # Время изменения объектов для листинга; нет записи - «давно» (2000 год).
    modified: dict[str, datetime] = field(default_factory=dict)

    async def put(self, area: StorageArea, key: str, data: bytes, content_type: str) -> None:
        if self.fail_put:
            raise ConnectionError("S3 недоступен (фейк)")
        self.objects[(area, key)] = (data, content_type)

    async def get(self, area: StorageArea, key: str, *, max_bytes: int) -> bytes:
        if (area, key) not in self.objects:  # как S3Storage.get
            raise GenerationError("source.missing", retryable=False)
        data, _ = self.objects[(area, key)]
        if len(data) > max_bytes:
            raise GenerationError("source.too_large", retryable=False)
        return data

    async def put_stream(
        self, area: StorageArea, key: str, chunks: AsyncIterator[bytes], content_type: str
    ) -> None:
        if self.fail_put:
            raise ConnectionError("S3 недоступен (фейк)")
        data = bytearray()
        async for chunk in chunks:  # ошибка источника: объект не появляется, как при abort
            data += chunk
        self.objects[(area, key)] = (bytes(data), content_type)

    async def download_to(self, area: StorageArea, key: str, path: Path, *, max_bytes: int) -> None:
        data = await self.get(area, key, max_bytes=max_bytes)
        await asyncio.to_thread(path.write_bytes, data)

    async def delete(self, area: StorageArea, keys: Sequence[str]) -> None:
        if self.fail_delete:
            raise ConnectionError("S3 недоступен (фейк)")
        for key in keys:
            self.objects.pop((area, key), None)
            self.modified.pop(key, None)

    async def list_objects(
        self, area: StorageArea, prefix: str, *, limit: int, token: str | None = None
    ) -> ObjectPage:
        keys = sorted(k for a, k in self.objects if a is area and k.startswith(prefix))
        start = int(token) if token else 0
        chunk = keys[start : start + limit]
        epoch = datetime(2000, 1, 1, tzinfo=UTC)
        items = [StoredObject(k, self.modified.get(k, epoch)) for k in chunk]
        nxt = str(start + limit) if start + limit < len(keys) else None
        return ObjectPage(items, nxt)

    async def copy(self, area: StorageArea, source_key: str, target_key: str) -> None:
        if self.fail_copy:
            raise ConnectionError("S3 недоступен (фейк)")
        self.objects[(area, target_key)] = self.objects[(area, source_key)]

    def keys(self, area: StorageArea) -> list[str]:
        return [k for a, k in self.objects if a is area]


@dataclass
class FakeFetcher:
    """Отдаёт заранее заданные ответы по адресу."""

    responses: dict[str, bytes | GenerationError] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    async def fetch(self, uri: str) -> FetchedSource:
        self.calls.append(uri)
        result = self.responses.get(uri)
        if result is None:
            raise GenerationError("source.not_found", retryable=False)
        if isinstance(result, GenerationError):
            raise result
        return FetchedSource(result, None)


@dataclass
class FakeTranscoder:
    """«Аудио» — байты, где каждый байт = 10 мс; нарезка берёт срез."""

    duration_ms: int | None = None
    cuts: list[tuple[float, float]] = field(default_factory=list)
    reversed_cuts: list[tuple[float, float]] = field(default_factory=list)

    async def normalize(self, data: bytes) -> NormalizedMedia:
        if b"broken" in data:
            raise GenerationError("source.undecodable", retryable=False)
        return NormalizedMedia(b"fLaC" + data[4:], "audio/flac", "flac")

    async def probe_duration_ms(self, data: bytes) -> int:
        return self.duration_ms if self.duration_ms is not None else len(data) * 10

    async def cut_audio(
        self, data: bytes, *, start_s: float, duration_s: float, reverse: bool = False
    ) -> bytes:
        self.cuts.append((start_s, duration_s))
        start = round(start_s * 100)
        cut = data[start : start + round(duration_s * 100)]
        if reverse:
            self.reversed_cuts.append((start_s, duration_s))
            return cut[::-1]
        return cut


@dataclass
class FakeImageProcessor:
    """Записывает операции; «рендер» — описание операции в байтах."""

    ops: list[ImageOps] = field(default_factory=list)

    async def normalize(self, data: bytes) -> NormalizedMedia:
        if b"broken" in data:
            raise GenerationError("source.undecodable", retryable=False)
        return NormalizedMedia(b"\x89PNG\r\n\x1a\n" + data, "image/png", "png")

    async def render(self, data: bytes, ops: ImageOps) -> RenderedImage:
        self.ops.append(ops)
        payload = f"{ops.crop}|{ops.blur_px}|{ops.pixel_block}|{ops.max_width}".encode()
        return RenderedImage(payload, ops.max_width, ops.max_width)


@dataclass
class FakeUgcEncoder:
    """«Кодек» UGC: байты `bad` - не изображение, иначе результат = `webp:` + вход."""

    calls: int = 0

    async def encode(self, data: bytes, purpose: UgcPurpose) -> EncodedImage:
        self.calls += 1
        if data.startswith(b"bad"):
            raise InvalidUgcImageError("файл не является допустимым изображением")
        out = b"webp:" + data
        return EncodedImage(out, purpose.output_side_px, purpose.output_side_px, "0" * 64)


class FakeSigner:
    """Подпись без секрета: URL содержит ключ и срок."""

    def sign(self, object_key: str, expires_at: datetime) -> str:
        return f"https://cdn.test/media/{object_key}?expires={int(expires_at.timestamp())}"


@dataclass
class FakeLimiter:
    """Счётчик в памяти без окна (окно проверяет integration-тест Redis)."""

    counts: dict[str, int] = field(default_factory=dict)

    async def hit(self, key: str, window_s: int) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


class SeqTokens:
    """Детерминированные «случайные» токены."""

    def __init__(self) -> None:
        self._n = 0

    def token_hex(self) -> str:
        self._n += 1
        return f"{self._n:032x}"


# ---------------------------------------------------------------- сборка use-cases на фейках
TEST_PROFILES: dict[str, Any] = {
    "version": 1,
    "profiles": [
        {
            "clue_type": "audio_intro",
            "profile_key": "default",
            "domains": ["music"],
            "params": {"start_s": 0, "levels": [{"seconds": 0.5}, {"seconds": 1}, {"seconds": 2}]},
        },
        {
            "clue_type": "frame_progressive",
            "profile_key": "default",
            "domains": ["film"],
            "params": {
                "levels": [
                    {"blur_px": 30, "crop": 0.4, "max_width": 640},
                    {"blur_px": 10, "crop": 0.7, "max_width": 640},
                    {"blur_px": 0, "crop": 1, "max_width": 640},
                ]
            },
        },
        {
            "clue_type": "poster_masked",
            "profile_key": "default",
            "domains": ["film"],
            "params": {
                "levels": [{"pixel_block": 32}, {"pixel_block": 8}, {"pixel_block": 1}],
                "mask_regions": [{"x": 0, "y": 0.8, "w": 1, "h": 0.2}],
            },
        },
    ],
}


class MediaHarness:
    """Use-cases сервиса, собранные на фейках портов."""

    def __init__(  # noqa: PLR0915 - сборка use-cases harness
        self,
        *,
        max_attempts: int = 3,
        link_max: int = 3,
        max_profiles: int = 3,
        backfill_batch: int = 2,
        profiles: dict[str, Any] | None = None,
        package_limits: Any = None,
    ) -> None:

        from media_pipeline_service.application.commands.catalog import (
            DisableItemAssets,
            PlanAssetSets,
            RegisterSourceFromCatalog,
            RestoreItemAssets,
            SyncMediaItem,
        )
        from media_pipeline_service.application.commands.generation import (
            GenerateAssetSet,
            RegenerateAssetSet,
        )
        from media_pipeline_service.application.commands.links import (
            IssueAssetLink,
            PurgeLinkJournal,
        )
        from media_pipeline_service.application.commands.profiles import (
            ApplyClueAssetsRequested,
            BackfillDynamicProfiles,
        )
        from media_pipeline_service.application.commands.sources import (
            SetSourcePosition,
            UploadSource,
        )
        from media_pipeline_service.application.generators import (
            AudioIntroGenerator,
            DescriptionGenerator,
            EmojiGenerator,
            FrameProgressiveGenerator,
            FrameSequenceGenerator,
            GeneratorRegistry,
            PosterMaskedGenerator,
        )
        from media_pipeline_service.application.profiles import ProfileDirectory
        from media_pipeline_service.application.queries.asset_sets import ListAssetSets
        from media_pipeline_service.application.queries.profiles import ListProfiles
        from media_pipeline_service.application.sources import SourceIngestor
        from media_pipeline_service.domain.links import LinkPolicy
        from media_pipeline_service.domain.sources import SourceKind

        from gv_testkit import FakeClock, InMemoryTaskQueue

        self.db = MemoryDb()
        self.clock = FakeClock()
        self.queue = InMemoryTaskQueue()
        self.storage = FakeStorage()
        self.fetcher = FakeFetcher()
        self.transcoder = FakeTranscoder()
        self.images = FakeImageProcessor()
        self.limiter = FakeLimiter()
        self.tokens = SeqTokens()
        self.registry = GeneratorRegistry(
            [
                AudioIntroGenerator(self.transcoder),
                FrameProgressiveGenerator(self.images),
                FrameSequenceGenerator(self.images),
                PosterMaskedGenerator(self.images, max_width=480),
                DescriptionGenerator(),
                EmojiGenerator(),
            ]
        )
        self.profiles = self.registry.build_catalog(profiles or TEST_PROFILES)
        self.directory = ProfileDirectory(self.profiles, self.registry)

        # Тест может задать хуки сбоев коммита (см. ``FakeMediaUnitOfWork``).
        self.fail_commit: Callable[[MemoryDb], None] | None = None
        self.after_commit: Callable[[MemoryDb], None] | None = None

        def uow() -> FakeMediaUnitOfWork:
            return FakeMediaUnitOfWork(
                self.db, self.clock.now(), self.fail_commit, self.after_commit
            )

        self.uow = uow
        self.ingestor = SourceIngestor(
            self.storage,
            {
                SourceKind.AUDIO: self.transcoder,
                SourceKind.SOUNDTRACK: self.transcoder,
                SourceKind.AUDIO_CLIP: self.transcoder,
                SourceKind.FRAME: self.images,
                SourceKind.POSTER: self.images,
            },
            self.tokens,
            max_bytes=10_000,
        )
        self.plan = PlanAssetSets(
            uow,
            self.clock,
            self.directory,
            self.queue,
            self.storage,
            registry=self.registry,
            scoped_clue_types=frozenset({"emoji"}),
        )
        self.disable = DisableItemAssets(uow, self.clock, self.storage)
        self.restore = RestoreItemAssets(uow, self.clock, self.queue)
        self.register = RegisterSourceFromCatalog(uow, self.clock)
        self.sync = SyncMediaItem(
            uow,
            self.clock,
            register_sources=self.register,
            plan=self.plan,
            disable=self.disable,
            restore=self.restore,
        )
        self.generate = GenerateAssetSet(
            uow,
            self.clock,
            registry=self.registry,
            storage=self.storage,
            fetcher=self.fetcher,
            ingestor=self.ingestor,
            tokens=self.tokens,
            max_attempts=max_attempts,
            max_source_bytes=10_000,
        )
        self.regenerate = RegenerateAssetSet(uow, self.clock, self.queue, self.storage)
        self.upload = UploadSource(
            uow,
            self.clock,
            self.ingestor,
            self.directory,
            self.plan,
            storage=self.storage,
        )
        self.set_position = SetSourcePosition(uow, self.clock, self.plan, storage=self.storage)

        from media_pipeline_service.application.archive import ArchiveLimits
        from media_pipeline_service.application.commands.packages import (
            ProcessPackage,
            SubmitPackage,
            SweepOrphanPackageArchives,
        )
        from media_pipeline_service.application.queries.packages import GetPackage

        self.package_limits = package_limits or ArchiveLimits(
            max_archive_bytes=1_000_000,
            max_entries=50,
            max_unpacked_bytes=5_000_000,
            max_entry_bytes=100_000,
            max_ratio=100,
            max_manifest_bytes=100_000,
        )
        self.submit_package = SubmitPackage(
            uow, self.clock, self.storage, self.queue, self.tokens, limits=self.package_limits
        )
        self.process_package = ProcessPackage(
            uow,
            self.clock,
            self.storage,
            self.upload,
            limits=self.package_limits,
            max_upload_bytes=self.package_limits.max_entry_bytes,
            keep_errors=3,
            lease=timedelta(seconds=300),
        )
        self.sweep_package_orphans = SweepOrphanPackageArchives(
            uow, self.storage, self.clock, ttl=timedelta(hours=24)
        )
        self.get_package = GetPackage(uow)
        self.policy = LinkPolicy(ttl_s=60, max_per_window=link_max, window_s=60)
        self.issue_link = IssueAssetLink(
            uow,
            self.clock,
            FakeSigner(),
            self.limiter,
            self.policy,
        )
        self.purge_links = PurgeLinkJournal(uow, self.clock, retention=timedelta(days=7), batch=2)
        self.list_sets = ListAssetSets(uow)
        self.request_assets = ApplyClueAssetsRequested(
            uow, self.clock, self.directory, max_profiles=max_profiles, max_params_bytes=4096
        )
        self.backfill = BackfillDynamicProfiles(
            uow, self.clock, self.directory, self.plan, self.storage, batch=backfill_batch
        )
        self.list_profiles = ListProfiles(uow, self.directory)

        from media_pipeline_service.application.commands.ugc import (
            ModerateUgcImage,
            PreviewUgcImage,
            SubmitUgcImage,
            SweepOrphanUgcObjects,
            UgcObjectPurger,
            UgcUseCases,
        )
        from media_pipeline_service.application.queries.ugc import GetUgcImage, ListUgcQueue
        from media_pipeline_service.infrastructure.config import load_ugc_catalog

        self.ugc_catalog = load_ugc_catalog()
        self.ugc_encoder = FakeUgcEncoder()
        purger = UgcObjectPurger(uow, self.storage, batch=2)
        self.ugc = UgcUseCases(
            catalog=self.ugc_catalog,
            submit=SubmitUgcImage(
                uow,
                self.clock,
                self.ugc_encoder,
                self.storage,
                self.tokens,
                self.ugc_catalog,
                purger,
            ),
            moderate=ModerateUgcImage(
                uow, self.clock, self.storage, self.tokens, self.ugc_catalog, purger
            ),
            preview=PreviewUgcImage(uow, self.clock, self.storage),
            queue=ListUgcQueue(uow),
            card=GetUgcImage(uow),
            purger=purger,
            orphans=SweepOrphanUgcObjects(
                uow, self.storage, self.clock, ttl=timedelta(hours=24), page_size=2
            ),
        )

    @property
    def sync_collection(self) -> SyncCollectionAssets:
        """Свои загадки сборников (ADR 0029): лимит графем как в настройках по умолчанию."""
        from media_pipeline_service.application.commands.collections import (
            SyncCollectionAssets,
        )

        return SyncCollectionAssets(
            self.uow, self.clock, self.plan, self.storage, emoji_max_graphemes=12
        )

    @property
    def list_sources(self) -> ListSources:
        """Исходники единицы для админки."""
        from media_pipeline_service.application.queries.sources import ListSources

        return ListSources(self.uow)

    async def run_tasks(self) -> int:
        """Выполнить все поставленные задачи генерации (повторы — как ретраи очереди)."""
        from uuid import UUID

        done = 0
        while self.queue.tasks:
            task = self.queue.tasks.pop(0)
            payload = task["payload"]
            await self.generate.execute(UUID(payload["asset_set_id"]), int(payload["generation"]))
            done += 1
        return done


@pytest.fixture
def media() -> MediaHarness:
    """Use-cases на фейках портов (unit)."""
    return MediaHarness()


@pytest.fixture
def media_factory() -> type[MediaHarness]:
    """Конструктор harness с параметрами (``max_attempts``, ``link_max``,
    ``max_profiles``, ``backfill_batch``, ``profiles``)."""
    return MediaHarness
