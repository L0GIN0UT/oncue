"""Гостевые сессии на реальном Postgres: схема 0006, репозиторий, очистка, периодическая задача."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from identity_service.application.commands.guest import PurgeGuestSessions
from identity_service.application.commands.guest_issuer import GuestConfig
from identity_service.domain import (
    DisplayName,
    EmailAddress,
    Nickname,
    RefreshToken,
    Session,
    SessionKind,
    User,
)
from identity_service.domain.guest import open_guest_session
from identity_service.infrastructure.db.guest_sessions import SqlGuestSessionPurger
from identity_service.infrastructure.db.repositories import SqlIdentityUnitOfWork
from identity_service.main import DEFINITION
from identity_service.settings import Settings
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gv_core.ids import uuid7
from gv_service import Infrastructure, JobResult
from gv_testkit import FakeClock, apply_migrations, run_periodic_job

pytestmark = pytest.mark.integration

CONFIG = GuestConfig(refresh_ttl_s=3600, revoked_retention_s=600, purge_batch=2)


@pytest.fixture
async def migrated(database_url: str) -> str:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    return database_url


@pytest.fixture
async def factory(migrated: str) -> AsyncIterator[async_sessionmaker[Any]]:
    engine = create_async_engine(migrated)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _uow(factory: async_sessionmaker[Any], clock: FakeClock) -> SqlIdentityUnitOfWork:
    return SqlIdentityUnitOfWork(factory, producer="identity", clock=clock)


def _guest(clock: FakeClock) -> Session:
    return open_guest_session(
        session_id=uuid7(), guest_id=uuid7(), now=clock.now(), ip_prefix="10.0.0.0/24"
    )


async def _add(factory: async_sessionmaker[Any], clock: FakeClock, *items: Session) -> None:
    async with _uow(factory, clock) as uow:
        for session in items:
            await uow.sessions.add(session)
            await uow.sessions.add_refresh(
                RefreshToken(
                    id=uuid7(),
                    session_id=session.id,
                    token_hash=uuid7().hex * 2,
                    parent_id=None,
                    created_at=clock.now(),
                    expires_at=clock.now() + timedelta(seconds=CONFIG.refresh_ttl_s),
                )
            )
        await uow.commit()


async def _user(factory: async_sessionmaker[Any], clock: FakeClock) -> Session:
    user = User.register(
        uuid7(),
        nickname=Nickname.parse("neo_2026"),
        email=EmailAddress.parse("neo@example.com"),
        display_name=DisplayName.parse("Neo"),
        locale=None,
        roles=frozenset({"user"}),
        now=clock.now(),
    )
    async with _uow(factory, clock) as uow:
        await uow.users.add(user)
        await uow.commit()
    return Session(
        id=uuid7(),
        user_id=user.id,
        kind=SessionKind.WEB,
        created_at=clock.now(),
        last_seen_at=clock.now(),
    )


async def _ids(factory: async_sessionmaker[Any]) -> set[Any]:
    async with factory() as s:
        return set((await s.execute(sa.text("SELECT id FROM sessions"))).scalars())


async def test_guest_session_roundtrip(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    guest = _guest(fake_clock)
    await _add(factory, fake_clock, guest)
    async with _uow(factory, fake_clock) as uow:
        loaded = await uow.sessions.get(guest.id)
    assert loaded is not None
    assert (loaded.guest_id, loaded.user_id, loaded.is_guest) == (guest.guest_id, None, True)
    async with factory() as s:
        kind = (
            await s.execute(
                sa.text("SELECT subject_kind FROM sessions WHERE id = :i"), {"i": guest.id}
            )
        ).scalar_one()
    assert kind == "guest"


@pytest.mark.parametrize(
    ("subject", "with_user", "with_guest", "kind", "seen"),
    [
        ("guest", True, True, "web", True),  # гость с user_id
        ("guest", False, False, "web", True),  # гость без guest_id
        ("guest", False, True, "admin", True),  # гостевая admin-сессия
        ("guest", False, True, "web", False),  # без last_seen_at очистка его бы не нашла
        ("user", True, True, "web", True),  # пользователь с guest_id
        ("robot", True, False, "web", True),  # неизвестный вид субъекта
    ],
)
async def test_schema_rejects_inconsistent_subject(  # noqa: PLR0917 — строка параметров
    factory: async_sessionmaker[Any],
    fake_clock: FakeClock,
    subject: str,
    with_user: bool,
    with_guest: bool,
    kind: str,
    seen: bool,
) -> None:
    user_session = await _user(factory, fake_clock)
    await _add(factory, fake_clock, user_session)
    sql = sa.text(
        "INSERT INTO sessions "
        "(id, created_at, subject_kind, user_id, guest_id, kind, last_seen_at) "
        "VALUES (:id, now(), :subject, :user_id, :guest_id, :kind, :seen)"
    )
    params = {
        "id": uuid7(),
        "subject": subject,
        "user_id": user_session.user_id if with_user else None,
        "guest_id": uuid7() if with_guest else None,
        "kind": kind,
        "seen": fake_clock.now() if seen else None,
    }
    async with factory() as s:
        with pytest.raises(IntegrityError):
            await s.execute(sql, params)


async def test_guest_id_unique(factory: async_sessionmaker[Any], fake_clock: FakeClock) -> None:
    first = _guest(fake_clock)
    await _add(factory, fake_clock, first)
    twin = open_guest_session(
        session_id=uuid7(), guest_id=first.guest_id or uuid7(), now=fake_clock.now(), ip_prefix=None
    )
    with pytest.raises(IntegrityError):
        await _add(factory, fake_clock, twin)


async def test_purge_removes_only_abandoned_guest_sessions(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    user_session = await _user(factory, fake_clock)
    idle = [_guest(fake_clock) for _ in range(3)]
    revoked_old = _guest(fake_clock)
    await _add(factory, fake_clock, user_session, *idle, revoked_old)

    fake_clock.advance(seconds=CONFIG.refresh_ttl_s - 1000)
    revoked_old.revoke(fake_clock.now(), "logout")
    fresh = _guest(fake_clock)
    revoked_recent = _guest(fake_clock)
    await _add(factory, fake_clock, fresh, revoked_recent)
    async with _uow(factory, fake_clock) as uow:
        await uow.sessions.save(revoked_old)
        await uow.commit()

    fake_clock.advance(seconds=1000)  # idle — ровно refresh TTL, revoked_old — 1000 с назад
    revoked_recent.revoke(fake_clock.now(), "logout")
    async with _uow(factory, fake_clock) as uow:
        await uow.sessions.save(revoked_recent)
        await uow.commit()

    deleted = await PurgeGuestSessions(SqlGuestSessionPurger(factory), fake_clock, CONFIG)()

    assert deleted == 4  # пачками по 2: 2 + 2 + 0
    assert await _ids(factory) == {user_session.id, fresh.id, revoked_recent.id}
    async with factory() as s:
        orphans = (
            await s.execute(
                sa.text(
                    "SELECT count(*) FROM refresh_tokens r "
                    "LEFT JOIN sessions s ON s.id = r.session_id WHERE s.id IS NULL"
                )
            )
        ).scalar_one()
    assert orphans == 0  # refresh-токены удалены каскадом


async def test_purge_job_registered_and_runs(migrated: str, fake_clock: FakeClock) -> None:
    settings = Settings(environment="ci", database_url=SecretStr(migrated))
    infra = await Infrastructure.open(settings, DEFINITION.tables, clock=fake_clock)
    try:
        assert infra.sessions is not None
        stale = _guest(fake_clock)
        await _add(infra.sessions, fake_clock, stale)
        fake_clock.advance(seconds=settings.guest.refresh_ttl_s)
        result = await run_periodic_job(DEFINITION, "guest_sessions.purge", infra)
        assert result is JobResult.OK
        assert stale.id not in await _ids(infra.sessions)
    finally:
        await infra.close()


async def test_downgrade_drops_guests_and_restores_not_null(migrated: str) -> None:
    import asyncio

    from alembic import command

    from gv_db.migrations import alembic_config

    clock = FakeClock()
    engine = create_async_engine(migrated)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        user_session = await _user(factory, clock)
        await _add(factory, clock, user_session, _guest(clock))
        assert DEFINITION.migrations_path is not None
        config = alembic_config(DEFINITION.migrations_path, migrated)
        await asyncio.to_thread(command.downgrade, config, "0005")
        assert await _ids(factory) == {user_session.id}
        async with factory() as s:
            nullable = (
                await s.execute(
                    sa.text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_name = 'sessions' AND column_name = 'user_id'"
                    )
                )
            ).scalar_one()
        assert nullable == "NO"
        await asyncio.to_thread(command.upgrade, config, "head")
    finally:
        await engine.dispose()
