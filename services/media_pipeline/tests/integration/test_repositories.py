"""Репозитории и Unit of Work на реальном PostgreSQL (миграции сервиса)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from media_pipeline_service.application.commands.generation import RegenerateAssetSet
from media_pipeline_service.application.ports import ANY_SCOPE, AdminAction
from media_pipeline_service.domain.asset_set import AssetLevel, AssetSet, AssetSetStatus
from media_pipeline_service.domain.clues import FrameProgressiveParams
from media_pipeline_service.domain.collections import CollectionState, CollectionStatus
from media_pipeline_service.domain.errors import MediaItemNotFoundError
from media_pipeline_service.domain.links import LinkPolicy, asset_object_key
from media_pipeline_service.domain.media_items import ItemStatus, MediaItemState
from media_pipeline_service.domain.profiles import AssetProfile
from media_pipeline_service.domain.sources import (
    AssetScope,
    SourceKind,
    SourceMedia,
    SourceOrigin,
)
from media_pipeline_service.infrastructure.db.repositories import SqlMediaUnitOfWork
from media_pipeline_service.infrastructure.db.tables import (
    TABLES,
    admin_actions,
    asset_levels,
    link_issuance,
)
from media_pipeline_service.main import DEFINITION
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gv_core import uuid7
from gv_core.errors import ConflictError, DependencyUnavailableError
from gv_db import create_engine, create_session_factory
from gv_testkit import FakeClock, InMemoryTaskQueue, apply_migrations, outbox_envelopes

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


@pytest.fixture
async def sessions(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    engine = create_engine(database_url)
    yield create_session_factory(engine)
    await engine.dispose()


def uow(sessions: async_sessionmaker[AsyncSession]) -> SqlMediaUnitOfWork:
    return SqlMediaUnitOfWork(producer="media_pipeline", clock=FakeClock(NOW), sessions=sessions)


def profile() -> AssetProfile:
    return AssetProfile(
        clue_type="frame_progressive",
        profile_key="default",
        domains=frozenset({"film"}),
        source_kind=SourceKind.FRAME,
        params=FrameProgressiveParams.from_mapping(
            {
                "levels": [
                    {"blur_px": 10, "crop": 0.5, "max_width": 640},
                    {"blur_px": 0, "crop": 1, "max_width": 640},
                ]
            }
        ),
    )


async def seed(sessions: async_sessionmaker[AsyncSession]) -> tuple[UUID, UUID]:
    item, source_id = uuid7(), uuid7()
    async with uow(sessions) as u:
        await u.items.upsert(
            MediaItemState(
                item,
                domain="film",
                kind="film",
                status=ItemStatus.ACTIVE,
                version=1,
                updated_at=NOW,
            )
        )
        await u.sources.add(
            SourceMedia(
                source_id,
                media_item_id=item,
                kind=SourceKind.FRAME,
                origin=SourceOrigin.TMDB,
                uri="https://image.tmdb.org/x.jpg",
                created_at=NOW,
            )
        )
        await u.commit()
    return item, source_id


async def test_asset_set_lifecycle_persists_levels_and_events(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    item, source_id = await seed(sessions)
    aggregate = AssetSet.plan(
        uuid7(), media_item_id=item, profile=profile(), source_media_ids=(source_id,), now=NOW
    )
    async with uow(sessions) as u:
        await u.asset_sets.add(aggregate)
        await u.commit()

    levels = [
        AssetLevel(
            level=i,
            object_key=asset_object_key(uuid7().hex, "webp"),
            mime="image/webp",
            size_bytes=10,
            width=640,
            height=360,
        )
        for i in (1, 2)
    ]
    async with uow(sessions) as u:
        loaded = await u.asset_sets.get(aggregate.id, for_update=True)
        assert loaded is not None
        loaded.start_attempt(1, NOW)
        loaded.complete(1, levels, NOW, event_version=1)
        await u.asset_sets.save(loaded)
        u.track(loaded)
        await u.commit()

    async with uow(sessions) as u:
        again = await u.asset_sets.get(aggregate.id)
        assert again is not None
        assert again.status is AssetSetStatus.READY
        assert again.levels == tuple(levels)
        assert again.params == aggregate.params
        (listed,) = await u.asset_sets.list_for_item(item, "frame_progressive")
        assert listed.id == aggregate.id
        assert await u.asset_sets.list_for_item(item, "poster_masked") == []
    async with sessions() as session:
        (envelope,) = await outbox_envelopes(session, TABLES.outbox)
    assert envelope.event_type == "media_pipeline.AssetsReady"
    assert envelope.aggregate_id == str(item)
    assert envelope.topic == "media_pipeline.asset_set.v1"


async def test_optimistic_lock_and_unique_profile(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    item, source_id = await seed(sessions)
    aggregate = AssetSet.plan(
        uuid7(), media_item_id=item, profile=profile(), source_media_ids=(source_id,), now=NOW
    )
    async with uow(sessions) as u:
        await u.asset_sets.add(aggregate)
        await u.commit()
    async with uow(sessions) as first, uow(sessions) as second:
        a = await first.asset_sets.get(aggregate.id)
        b = await second.asset_sets.get(aggregate.id)
        assert a is not None and b is not None
        a.disable(NOW)
        await first.asset_sets.save(a)
        await first.commit()
        b.regenerate(NOW)
        with pytest.raises(ConflictError):
            await second.asset_sets.save(b)
    duplicate = AssetSet.plan(
        uuid7(), media_item_id=item, profile=profile(), source_media_ids=(source_id,), now=NOW
    )
    with pytest.raises(Exception, match="uq_asset_sets"):
        async with uow(sessions) as u:
            await u.asset_sets.add(duplicate)


async def test_projection_upsert_keeps_newest_version(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    item, _ = await seed(sessions)
    async with uow(sessions) as u:
        stale = MediaItemState(
            item, domain="film", kind="film", status=ItemStatus.BLOCKED, version=1, updated_at=NOW
        )
        await u.items.upsert(stale)  # гонка: та же версия не перезаписывает
        newer = MediaItemState(
            item, domain="film", kind="film", status=ItemStatus.BLOCKED, version=5, updated_at=NOW
        )
        await u.items.upsert(newer)
        await u.commit()
    async with uow(sessions) as u:
        state = await u.items.get(item)
    assert state is not None and (state.status, state.version) == (ItemStatus.BLOCKED, 5)


async def test_sources_add_is_idempotent_and_save_updates(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    item, source_id = await seed(sessions)
    async with uow(sessions) as u:
        source = await u.sources.get(source_id)
        assert source is not None
        await u.sources.add(source)  # повтор события
        source.mark_stored(
            object_key="s/" + "0" * 32 + ".png", sha256="f" * 64, size_bytes=5, mime="image/png"
        )
        await u.sources.save(source)
        await u.commit()
    async with uow(sessions) as u:
        (stored,) = await u.sources.list_for_item(item)
    assert stored.is_stored and stored.size_bytes == 5


async def test_link_journal_purge_and_idempotency_and_audit(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    asset_set_id = uuid7()
    subject_hash = LinkPolicy.subject_hash("game:42")
    async with uow(sessions) as u:
        for days in (10, 8, 1):
            at = NOW - timedelta(days=days)
            await u.links.record(
                asset_set_id=asset_set_id,
                level=1,
                subject_hash=subject_hash,
                issued_at=at,
                expires_at=at,
            )
        assert await u.links.purge_older_than(NOW - timedelta(days=7), limit=1) == 1
        assert await u.links.purge_older_than(NOW - timedelta(days=7), limit=10) == 1
        assert await u.links.purge_older_than(NOW - timedelta(days=7), limit=10) == 0

        actor = uuid7()
        assert await u.idempotency.find(actor, "key-00001") is None
        await u.idempotency.save(actor, "key-00001", "h" * 64, {"ok": True}, status_code=202)
        done = await u.idempotency.find(actor, "key-00001")
        assert done is not None and (done.status_code, done.response) == (202, {"ok": True})

        await u.audit.record(
            AdminAction(
                actor_id=uuid7(),
                action="asset_set.regenerate",
                target_id=asset_set_id,
                reason="тест",
                at=NOW,
            )
        )
        await u.commit()
    async with sessions() as session:
        query = select(link_issuance.c.subject_hash).where(
            link_issuance.c.asset_set_id == asset_set_id
        )
        stored = (await session.execute(query)).scalars().all()
    assert stored == [subject_hash]  # в журнале только хэш субъекта


async def test_link_journal_rejects_raw_subject(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with uow(sessions) as u:
        with pytest.raises(IntegrityError, match="subject_hash_sha256"):
            await u.links.record(
                asset_set_id=uuid7(), level=1, subject_hash="game:42", issued_at=NOW, expires_at=NOW
            )


async def test_next_event_version_is_per_item_and_rolls_back(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    item, _ = await seed(sessions)
    other, _ = await seed(sessions)
    async with uow(sessions) as u:
        assert [await u.items.next_event_version(item) for _ in range(2)] == [1, 2]
        assert await u.items.next_event_version(other) == 1
        await u.commit()
    async with uow(sessions) as u:  # откат транзакции откатывает и счётчик
        assert await u.items.next_event_version(item) == 3
    async with uow(sessions) as u:
        assert await u.items.next_event_version(item) == 3
        # upsert проекции событием catalog не сбрасывает счётчик
        await u.items.upsert(
            MediaItemState(
                item,
                domain="film",
                kind="film",
                status=ItemStatus.ACTIVE,
                version=7,
                updated_at=NOW,
            )
        )
        await u.commit()
    async with uow(sessions) as u:
        assert await u.items.next_event_version(item) == 4
        with pytest.raises(MediaItemNotFoundError):
            await u.items.next_event_version(uuid7())


class _FlakyQueue(InMemoryTaskQueue):
    """Очередь, первая постановка в которую падает (после записи мутации в транзакции)."""

    def __init__(self) -> None:
        super().__init__()
        self.failures = 1

    async def enqueue(self, task_type: str, payload: dict[str, Any], *, task_id: str) -> None:
        if self.failures:
            self.failures -= 1
            raise DependencyUnavailableError("RabbitMQ недоступен")
        await super().enqueue(task_type, payload, task_id=task_id)


async def test_regenerate_failure_after_mutation_does_not_stick_idempotency_key(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Регресс: ключ резервировался отдельной транзакцией и оставался «в процессе» навсегда.

    Теперь ключ пишется в транзакции мутации: сбой после изменения набора откатывает всё,
    повтор с тем же ключом выполняет мутацию заново, следующий — получает сохранённый ответ.
    """
    item, source_id = await seed(sessions)
    aggregate = AssetSet.plan(
        uuid7(), media_item_id=item, profile=profile(), source_media_ids=(source_id,), now=NOW
    )
    async with uow(sessions) as u:
        await u.asset_sets.add(aggregate)
        await u.commit()
    queue = _FlakyQueue()
    regenerate = RegenerateAssetSet(lambda: uow(sessions), FakeClock(NOW), queue, _NoStorage())
    actor, key = uuid7(), f"regen-{uuid7().hex}"

    with pytest.raises(DependencyUnavailableError):
        await regenerate.execute(aggregate.id, actor_id=actor, reason="кадр", idempotency_key=key)
    async with uow(sessions) as u:
        assert await u.idempotency.find(actor, key) is None
        unchanged = await u.asset_sets.get(aggregate.id)
        assert unchanged is not None and unchanged.generation == 1

    view = await regenerate.execute(
        aggregate.id, actor_id=actor, reason="кадр", idempotency_key=key
    )
    replay = await regenerate.execute(
        aggregate.id, actor_id=actor, reason="кадр", idempotency_key=key
    )
    assert view.generation == 2 and replay == view
    assert len(queue.tasks) == 1
    async with sessions() as session:
        audit = (
            (
                await session.execute(
                    select(admin_actions.c.action).where(admin_actions.c.target_id == aggregate.id)
                )
            )
            .scalars()
            .all()
        )
    assert audit == ["asset_set.regenerate"]  # мутация выполнена ровно один раз


class _NoStorage:
    async def put(self, area: Any, key: str, data: bytes, content_type: str) -> None:
        raise AssertionError("не используется")

    async def get(self, area: Any, key: str, *, max_bytes: int) -> bytes:
        raise AssertionError("не используется")

    async def delete(self, area: Any, keys: Any) -> None:
        return None


async def test_external_session_uses_savepoint(sessions: async_sessionmaker[AsyncSession]) -> None:
    item, _ = await seed(sessions)
    clock = FakeClock(NOW)
    async with sessions() as session, session.begin():
        inner = SqlMediaUnitOfWork(producer="media_pipeline", clock=clock, session=session)
        async with inner as u:  # без commit — SAVEPOINT откатывается, внешняя транзакция жива
            await u.items.upsert(
                MediaItemState(
                    item,
                    domain="film",
                    kind="film",
                    status=ItemStatus.BLOCKED,
                    version=9,
                    updated_at=NOW,
                )
            )
        ok = SqlMediaUnitOfWork(producer="media_pipeline", clock=clock, session=session)
        async with ok as u:
            await u.items.upsert(
                MediaItemState(
                    item,
                    domain="film",
                    kind="film",
                    status=ItemStatus.ACTIVE,
                    version=3,
                    updated_at=NOW,
                )
            )
            await u.commit()
    async with uow(sessions) as u:
        state = await u.items.get(item)
    assert state is not None and state.version == 3


async def test_uow_requires_exactly_one_session_source(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(ValueError, match="ровно один"):
        SqlMediaUnitOfWork(producer="p", clock=FakeClock())
    async with sessions() as session:
        assert (await session.execute(select(1))).scalar() == 1


async def test_stage3a_source_fields_and_multi_source_sets_roundtrip(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """0005: позиция, происхождение, текстовое содержимое и все исходники набора."""
    item, frame_id = await seed(sessions)
    text_id, second_frame = uuid7(), uuid7()
    content = {"version": 1, "emoji": "🎬🚀", "genres": ["драма"], "year": 2014}
    async with uow(sessions) as u:
        await u.sources.add(
            SourceMedia(
                second_frame,
                media_item_id=item,
                kind=SourceKind.FRAME,
                origin=SourceOrigin.SEED,
                uri=None,
                created_at=NOW,
                position=2,
                source_url="https://example.org/frame-2",
                license_note="синтетика",
            )
        )
        await u.sources.add(
            SourceMedia.text(
                text_id,
                media_item_id=item,
                content=content,
                sha256="a" * 64,
                size_bytes=12,
                created_at=NOW,
            )
        )
        aggregate = AssetSet.plan(
            uuid7(),
            media_item_id=item,
            profile=profile(),
            source_media_ids=(frame_id, second_frame),
            now=NOW,
        )
        await u.asset_sets.add(aggregate)
        await u.commit()
    async with uow(sessions) as u:
        stored = {s.id: s for s in await u.sources.list_for_item(item)}
        loaded = await u.asset_sets.get(aggregate.id)
        await u.sources.delete([text_id])
        await u.commit()
    assert stored[second_frame].position == 2
    assert stored[second_frame].origin is SourceOrigin.SEED
    assert (stored[second_frame].source_url, stored[second_frame].license_note) == (
        "https://example.org/frame-2",
        "синтетика",
    )
    assert stored[frame_id].position == 1 and stored[frame_id].text_content is None
    assert stored[text_id].text_content == content and stored[text_id].is_usable
    assert loaded is not None
    assert loaded.source_media_ids == (frame_id, second_frame)
    assert loaded.source_media_id == frame_id
    async with uow(sessions) as u:
        assert await u.sources.get(text_id) is None


async def test_dynamic_profiles_repository(sessions: async_sessionmaker[AsyncSession]) -> None:
    from media_pipeline_service.domain.dynamic_profiles import (
        DynamicProfile,
        DynamicProfileStatus,
    )

    item, source_id = await seed(sessions)

    def dynamic(hash_: str, status: DynamicProfileStatus, code: str | None) -> DynamicProfile:
        return DynamicProfile(
            uuid7(),
            clue_type="description",
            domain="film",
            params={"levels": [{"fields": ["year"]}]},
            params_hash=hash_,
            status=status,
            error_code=code,
            request_id=uuid7(),
            mode_key="film.random.description",
            actor_id=uuid7(),
            backfill_cursor=None,
            created_at=NOW,
            updated_at=NOW,
        )

    usable = dynamic("a" * 64, DynamicProfileStatus.BACKFILLING, None)
    rejected = dynamic("b" * 64, DynamicProfileStatus.REJECTED, "profile.limit_reached")
    async with uow(sessions) as u:
        await u.profiles.lock()
        await u.profiles.add(usable)
        await u.profiles.add(rejected)
        aggregate = AssetSet.plan(
            uuid7(), media_item_id=item, profile=profile(), source_media_ids=(source_id,), now=NOW
        )
        await u.asset_sets.add(aggregate)
        await u.commit()
    async with uow(sessions) as u:
        assert await u.profiles.count_usable() == 1
        assert [p.id for p in await u.profiles.list_usable("film")] == [usable.id]
        assert await u.profiles.list_usable("music") == []
        assert [p.id for p in await u.profiles.list_backfilling(10)] == [usable.id]
        assert len(await u.profiles.list_all("description")) == 2
        assert [p.id for p in await u.profiles.list_all(params_hash="b" * 64)] == [rejected.id]
        loaded = await u.profiles.get("description", "a" * 64, "film", for_update=True)
        assert loaded is not None and loaded.params == usable.params
        loaded.advance(item, done=True, now=NOW)
        await u.profiles.save(loaded)
        assert await u.items.list_active_ids("film", after=None, limit=10) == [item]
        assert await u.items.list_active_ids("film", after=item, limit=10) == []
        counts = await u.asset_sets.count_by_profile("frame_progressive")
        await u.commit()
    assert [(c.profile_key, c.status, c.count) for c in counts] == [("default", "pending", 1)]
    async with uow(sessions) as u:
        stored = await u.profiles.get("description", "a" * 64, "film")
        assert stored is not None
        assert (stored.status, stored.backfill_cursor) == (DynamicProfileStatus.ACTIVE, item)
    with pytest.raises(IntegrityError):
        async with uow(sessions) as u:
            await u.profiles.add(dynamic("a" * 64, DynamicProfileStatus.BACKFILLING, None))
            await u.commit()


async def test_scoped_sources_and_sets_are_separated_by_owner(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """ADR 0029: набор сборника живёт рядом с каталожным того же профиля, фильтры по
    владельцу, удаление по scope; проекция сборника отбрасывает старые версии."""
    item, source_id = await seed(sessions)
    scope, other = AssetScope.collection(uuid7()), AssetScope.collection(uuid7())
    content = {"version": 1, "emoji": "🚢🧊💔"}
    scoped_source = SourceMedia.text(
        uuid7(),
        media_item_id=item,
        content=content,
        sha256="0" * 64,
        size_bytes=10,
        created_at=NOW,
        scope=scope,
    )
    sets = {
        owner: AssetSet.plan(
            uuid7(),
            media_item_id=item,
            profile=profile(),
            source_media_ids=(source_id,),
            now=NOW,
            scope=owner,
        )
        for owner in (None, scope, other)
    }
    level = AssetLevel(
        level=1, object_key=asset_object_key(uuid7().hex, "json"), mime="x", size_bytes=1
    )
    sets[scope].levels = (level,)
    async with uow(sessions) as u:
        await u.sources.add(scoped_source)
        for aggregate in sets.values():
            await u.asset_sets.add(aggregate)
        await u.commit()

    async with uow(sessions) as u:
        assert [s.id for s in await u.sources.list_for_item(item)] == [source_id]
        (loaded_source,) = await u.sources.list_for_item(item, scope=scope)
        assert (loaded_source.scope, loaded_source.origin) == (scope, SourceOrigin.COLLECTION)
        assert loaded_source.text_content == content
        assert [s.id for s in await u.sources.list_for_scope(scope)] == [scoped_source.id]
        assert await u.sources.list_for_scope(other) == []
        assert await u.sources.scopes_for_item(item) == [scope]
        (catalog,) = await u.asset_sets.list_for_item(item, scope=None)
        assert catalog.id == sets[None].id and catalog.scope is None
        (mine,) = await u.asset_sets.list_for_scope(scope, for_update=True)
        assert (mine.id, mine.scope, mine.levels) == (sets[scope].id, scope, (level,))
        everything = await u.asset_sets.list_for_item(item, scope=ANY_SCOPE)
        assert {a.id for a in everything} == {a.id for a in sets.values()}
        assert list(await u.items.get_many([item, uuid7()], for_update=True)) == [item]
        await u.asset_sets.delete([mine.id])
        await u.commit()

    async with uow(sessions) as u:
        assert await u.asset_sets.list_for_scope(scope) == []
        assert [a.id for a in await u.asset_sets.list_for_scope(other)] == [sets[other].id]
    async with sessions() as session:
        query = select(asset_levels.c.level).where(asset_levels.c.asset_set_id == sets[scope].id)
        assert (await session.execute(query)).all() == []  # уровни — каскадом

    # второй исходник того же владельца у единицы запрещён схемой
    with pytest.raises(IntegrityError):
        async with uow(sessions) as u:
            await u.sources.add(
                SourceMedia.text(
                    uuid7(),
                    media_item_id=item,
                    content={"version": 1, "emoji": "🎬"},
                    sha256="1" * 64,
                    size_bytes=5,
                    created_at=NOW,
                    scope=scope,
                )
            )
    async with uow(sessions) as u:
        state = CollectionState(
            scope.id, version=3, status=CollectionStatus.PUBLISHED, updated_at=NOW
        )
        assert await u.collections.advance(state)
        assert not await u.collections.advance(state)  # та же версия
        stale = CollectionState(scope.id, version=2, status=CollectionStatus.HIDDEN, updated_at=NOW)
        assert not await u.collections.advance(stale)
        newer = CollectionState(
            scope.id, version=4, status=CollectionStatus.DELETED, updated_at=NOW
        )
        assert await u.collections.advance(newer)
        await u.commit()
