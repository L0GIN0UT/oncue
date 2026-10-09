"""Репозитории и Unit of Work на реальном PostgreSQL (миграции сервиса)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from media_pipeline_service.application.ports import AdminAction
from media_pipeline_service.domain.asset_set import AssetLevel, AssetSet, AssetSetStatus
from media_pipeline_service.domain.clues import FrameProgressiveParams
from media_pipeline_service.domain.links import asset_object_key
from media_pipeline_service.domain.media_items import ItemStatus, MediaItemState
from media_pipeline_service.domain.profiles import AssetProfile
from media_pipeline_service.domain.sources import SourceKind, SourceMedia, SourceOrigin
from media_pipeline_service.infrastructure.db.repositories import SqlMediaUnitOfWork
from media_pipeline_service.infrastructure.db.tables import TABLES
from media_pipeline_service.main import DEFINITION
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gv_core import uuid7
from gv_core.errors import ConflictError
from gv_db import create_engine, create_session_factory
from gv_testkit import FakeClock, apply_migrations, outbox_envelopes

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
        uuid7(), media_item_id=item, profile=profile(), source_media_id=source_id, now=NOW
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
        loaded.complete(1, levels, NOW)
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
        uuid7(), media_item_id=item, profile=profile(), source_media_id=source_id, now=NOW
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
        uuid7(), media_item_id=item, profile=profile(), source_media_id=source_id, now=NOW
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
    async with uow(sessions) as u:
        for days in (10, 8, 1):
            at = NOW - timedelta(days=days)
            await u.links.record(
                asset_set_id=asset_set_id, level=1, subject="s", issued_at=at, expires_at=at
            )
        assert await u.links.purge_older_than(NOW - timedelta(days=7), limit=1) == 1
        assert await u.links.purge_older_than(NOW - timedelta(days=7), limit=10) == 1
        assert await u.links.purge_older_than(NOW - timedelta(days=7), limit=10) == 0

        assert await u.idempotency.reserve("scope", "key-1", "h", NOW) is None
        pending = await u.idempotency.reserve("scope", "key-1", "h", NOW)
        assert pending is not None and not pending.completed
        await u.idempotency.complete("scope", "key-1", 202, {"ok": True})
        done = await u.idempotency.reserve("scope", "key-1", "other", NOW)
        assert done is not None and done.completed and done.response == {"ok": True}
        await u.idempotency.release("scope", "key-1")  # завершённый ключ не освобождается
        assert await u.idempotency.reserve("scope", "key-1", "h", NOW) is not None

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
