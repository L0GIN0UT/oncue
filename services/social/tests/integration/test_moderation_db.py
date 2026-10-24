"""Адаптеры БД: параллельные жалобы, миграции вниз/вверх, лимитер Redis, откат проекции версий."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from alembic import command
from social_service.infrastructure.db import tables_moderation as t
from social_service.infrastructure.limiter import RedisRateLimiter
from social_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from gv_core.errors import DependencyUnavailableError
from gv_core.ids import uuid7
from gv_db.migrations import alembic_config, run_migrations

if TYPE_CHECKING:
    from conftest import SocialService

    from gv_testkit import InfraProvider

pytestmark = pytest.mark.integration


async def test_parallel_reports_open_exactly_one_case(social: SocialService) -> None:
    owner = uuid7()
    collection = await social.publish_collection(owner)
    reports = social.container.reports()
    authors = [uuid7() for _ in range(10)]
    outcomes = await asyncio.gather(
        *(reports.file(a, "collection", collection, None, "spam", None) for a in authors)
    )
    assert not any(o.repeated for o in outcomes)
    (case,) = await social.rows(t.moderation_cases)
    assert case.target_id == collection
    assert await social.count(t.reports) == 10
    assert await social.count(t.case_reports, t.case_reports.c.case_id == case.id) == 10
    events = await social.outbox_events("social.ReportFiled")
    threshold = social.config.config.reports.threshold_per_day
    assert len(events) == 10
    # Кейс открыт на пороге: с case_id все жалобы, начиная с порогового.
    assert len([e for e in events if e.payload["case_id"] == str(case.id)]) == 10 - threshold + 1
    (counters,) = await social.rows(t.behaviour_counters)
    assert counters.reports_received == 10


async def test_parallel_duplicates_from_one_author_make_one_report(social: SocialService) -> None:
    collection = await social.publish_collection(uuid7())
    author = uuid7()
    reports = social.container.reports()
    outcomes = await asyncio.gather(
        *(reports.file(author, "collection", collection, None, "spam", None) for _ in range(5))
    )
    assert sorted(o.repeated for o in outcomes) == [False, True, True, True, True]
    assert len({o.id for o in outcomes}) == 1
    assert await social.count(t.reports) == 1
    assert len(await social.outbox_events("social.ReportFiled")) == 1


async def test_resolved_case_allows_next_one_but_open_is_unique(social: SocialService) -> None:
    assert social.infra.sessions is not None
    target = uuid7()
    row = {
        "target_kind": "collection",
        "target_id": target,
        "target_user_id": uuid7(),
        "status": "open",
        "opened_at": social.clock.now(),
        "version": 1,
    }
    async with social.infra.sessions() as session, session.begin():
        await session.execute(t.moderation_cases.insert().values(id=uuid7(), **row))
    with pytest.raises(IntegrityError):
        async with social.infra.sessions() as session, session.begin():
            await session.execute(t.moderation_cases.insert().values(id=uuid7(), **row))
    async with social.infra.sessions() as session, session.begin():
        await session.execute(
            t.moderation_cases.update()
            .where(t.moderation_cases.c.target_id == target)
            .values(status="resolved")
        )
        await session.execute(t.moderation_cases.insert().values(id=uuid7(), **row))


async def test_0003_downgrades_and_upgrades_again(social: SocialService, database_url: str) -> None:
    path = DEFINITION.migrations_path
    assert path is not None
    engine = create_async_engine(database_url)
    try:
        await asyncio.to_thread(command.downgrade, alembic_config(path, database_url), "0002")
        async with engine.connect() as conn:
            tables = set(
                (
                    await conn.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                    )
                ).scalars()
            )
        assert not tables & {"comments", "reports", "moderation_cases", "behaviour_counters"}
        await asyncio.to_thread(run_migrations, path, database_url)
    finally:
        await engine.dispose()


async def test_body_length_check_in_schema(social: SocialService) -> None:
    assert social.infra.sessions is not None
    with pytest.raises(DBAPIError):
        async with social.infra.sessions() as session, session.begin():
            await session.execute(
                t.comments.insert().values(
                    id=uuid7(),
                    collection_id=uuid7(),
                    author_id=uuid7(),
                    body="я" * 501,
                    created_at=social.clock.now(),
                )
            )


async def test_rate_limiter_counts_in_window_and_fails_closed(infra: InfraProvider) -> None:
    from redis.asyncio import Redis

    redis = Redis.from_url(infra.redis_url())
    try:
        limiter = RedisRateLimiter(lambda: redis, prefix=f"test:{uuid7().hex}:")
        assert [await limiter.hit("u", window_s=60) for _ in range(3)] == [1, 2, 3]
        assert await limiter.hit("other", window_s=60) == 1
    finally:
        await redis.aclose()
    with pytest.raises(DependencyUnavailableError):
        await RedisRateLimiter(lambda: None).hit("u", window_s=60)
