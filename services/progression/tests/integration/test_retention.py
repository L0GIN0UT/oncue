"""Ретенция ``metric_marks`` и ``quest_progress``: под рантайм-ролью, журналы не трогаются."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest
from progression_service.application.retention import RetentionPurger
from progression_service.infrastructure.db.repositories import SqlProgressionUnitOfWork
from progression_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gv_core.ids import uuid7
from gv_testkit import FakeClock, apply_migrations

pytestmark = pytest.mark.integration

Service = Any  # фикстура `progression` описана в conftest.py
ROLE = "progression_app"
PASSWORD = "app-role-test-password"
MARKS_DAYS = 30
QUESTS_DAYS = 20


async def _exec(sessions: Any, sql: str, **params: Any) -> None:
    async with sessions() as session, session.begin():
        await session.execute(text(sql), params)


async def _count(sessions: Any, table: str) -> int:
    async with sessions() as session:
        return int(await session.scalar(text(f"SELECT count(*) FROM {table}")))  # noqa: S608


async def _seed(sessions: Any, now: datetime) -> None:
    """Старые и свежие отметки; старые и свежие задания всех периодов."""
    user = uuid7()
    old, fresh = now - timedelta(days=MARKS_DAYS + 1), now - timedelta(days=MARKS_DAYS - 1)
    for key, at in (("old-1", old), ("old-2", old), ("old-3", old), ("new-1", fresh)):
        await _exec(
            sessions,
            "INSERT INTO metric_marks (subject_key, user_id, at) VALUES (:k, :u, :at)",
            k=key,
            u=user,
            at=at,
        )
    await _exec(
        sessions,
        "INSERT INTO config_versions (version, status, body, created_by, created_at) "
        "VALUES (1, 'archived', '{}', gen_random_uuid(), now()) ON CONFLICT DO NOTHING",
    )
    cutoff = (now - timedelta(days=QUESTS_DAYS)).date()
    rows = [
        ("q-old-daily", cutoff - timedelta(days=3), "daily"),
        ("q-old-weekly", cutoff - timedelta(days=9), "weekly"),
        ("q-new-daily", cutoff + timedelta(days=3), "daily"),
        ("q-one-time", date(2020, 1, 1), "one_time"),  # разовое: нужно для идемпотентности
    ]
    for key, start, period in rows:
        await _exec(
            sessions,
            "INSERT INTO quest_assignments (user_id, quest_key, period_start, period, title, "
            "description, metric, target, reward_xp, reward_verses, config_version, assigned_at) "
            "VALUES (:u, :k, :s, :p, 't', 'd', 'm', 1, 0, 0, 1, now())",
            u=user,
            k=key,
            s=start,
            p=period,
        )
        await _exec(
            sessions,
            "INSERT INTO quest_progress (user_id, quest_key, period_start, progress, updated_at) "
            "VALUES (:u, :k, :s, 1, now())",
            u=user,
            k=key,
            s=start,
        )


def _purger(
    factory: Any, clock: FakeClock, *, batch: int = 2
) -> tuple[RetentionPurger, list[float]]:
    pauses: list[float] = []

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)

    purger = RetentionPurger(
        factory,
        clock,
        marks_days=MARKS_DAYS,
        quest_progress_days=QUESTS_DAYS,
        batch=batch,
        pause_s=0.5,
        sleep=sleep,
    )
    return purger, pauses


async def test_purges_old_rows_in_batches_with_pauses(progression: Service) -> None:
    sessions = progression.infra.sessions
    await progression.publish_config()
    await _seed(sessions, progression.clock.now())
    purger, pauses = _purger(progression.container.uow_factory(), progression.clock)

    result = await purger.run()

    assert (result.metric_marks, result.quest_progress) == (3, 2)
    assert await _count(sessions, "metric_marks") == 1
    # Остались свежее и разовое; назначения (снимки) ретенция не трогает.
    assert await _count(sessions, "quest_progress") == 2
    assert await _count(sessions, "quest_assignments") == 4
    assert pauses == [0.5, 0.5]  # пауза после каждой полной пачки (2 из 3 marks, 2 из 2 quests)
    again = await purger.run()  # идемпотентно
    assert (again.metric_marks, again.quest_progress) == (0, 0)


async def test_ledgers_are_never_purged(progression: Service) -> None:
    await progression.publish_config()
    await progression.deliver(progression.game_finished(uuid7(), [100, 100]))
    progression.clock.advance(timedelta(days=3650))
    purger, _ = _purger(progression.container.uow_factory(), progression.clock)

    await purger.run()

    assert await progression.xp_rows() == 1
    assert await progression.verse_rows() == 1


async def test_purge_works_under_runtime_role_without_ledger_delete(database_url: str) -> None:
    """ADR 0060: DELETE на журналах закрыт, на остальных таблицах (включая ретенцию) — открыт."""
    assert DEFINITION.migrations_path is not None
    url = make_url(database_url)
    db_name = str(url.database)
    app_url = url.set(username=ROLE, password=PASSWORD).render_as_string(hide_password=False)
    admin = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f"CREATE ROLE {ROLE} LOGIN PASSWORD '{PASSWORD}'"))
        await conn.execute(text(f"GRANT CONNECT ON DATABASE {db_name} TO {ROLE}"))
    app = create_async_engine(app_url)
    try:
        await apply_migrations(database_url, DEFINITION.migrations_path)
        clock = FakeClock()
        owner_sessions = async_sessionmaker(admin, expire_on_commit=False)
        sessions = async_sessionmaker(app, expire_on_commit=False)

        def factory() -> SqlProgressionUnitOfWork:
            return SqlProgressionUnitOfWork(producer="progression", clock=clock, sessions=sessions)

        await _seed(owner_sessions, clock.now())
        purger, _ = _purger(factory, clock)
        result = await purger.run()
        assert (result.metric_marks, result.quest_progress) == (3, 2)
        async with app.connect() as conn:
            for table in ("metric_marks", "quest_progress", "reconcile_checkpoints"):
                assert await conn.scalar(
                    text("SELECT has_table_privilege(:r, :t, 'DELETE')"), {"r": ROLE, "t": table}
                )
            assert not await conn.scalar(
                text("SELECT has_table_privilege(:r, 'xp_ledger', 'DELETE')"), {"r": ROLE}
            )
    finally:
        await app.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f"REVOKE ALL ON DATABASE {db_name} FROM {ROLE}"))
            await conn.execute(text(f"DROP OWNED BY {ROLE}"))
            await conn.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))
        await admin.dispose()
