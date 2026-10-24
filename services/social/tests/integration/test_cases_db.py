"""Кейсы модерации на реальной БД: миграция 0005, гонки claim/resolve, аудит, проекция аватарок."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from alembic import command
from social_service.application.cases import SanctionSpec, Staff
from social_service.application.ports.cases import AvatarRef
from social_service.infrastructure.db import tables_moderation as t
from social_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from gv_core.errors import ConflictError
from gv_core.ids import uuid7
from gv_db.migrations import alembic_config, run_migrations

if TYPE_CHECKING:
    from conftest import SocialService

pytestmark = pytest.mark.integration

PERMS = frozenset({"social.moderate"})
REASON = "по правилам"


async def _case(social: SocialService) -> UUID:
    collection = await social.publish_collection(uuid7())
    reports = social.container.reports()
    for _ in range(social.config.config.reports.threshold_per_day):
        await reports.file(uuid7(), "collection", collection, None, "spam", None)
    (case,) = await social.rows(t.moderation_cases)
    return case.id  # type: ignore[no-any-return]


def _key() -> str:
    return f"key-{uuid7().hex}"


async def test_0005_downgrades_and_upgrades_again(social: SocialService, database_url: str) -> None:
    path = DEFINITION.migrations_path
    assert path is not None
    engine = create_async_engine(database_url)
    try:
        await asyncio.to_thread(command.downgrade, alembic_config(path, database_url), "0004")
        async with engine.connect() as conn:
            tables = set(
                (
                    await conn.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                    )
                ).scalars()
            )
        assert not tables & {"avatar_proj", "moderation_audit"}
        assert "moderation_cases" in tables  # 0003 на месте
        await asyncio.to_thread(run_migrations, path, database_url)
    finally:
        await engine.dispose()


async def test_parallel_claims_one_wins(social: SocialService) -> None:
    case_id = await _case(social)
    cases = social.container.cases
    moderators = [uuid7() for _ in range(6)]
    results = await asyncio.gather(
        *(cases.claim(Staff(m, PERMS), case_id, 1, _key()) for m in moderators),
        return_exceptions=True,
    )
    winners = [r for r in results if isinstance(r, dict)]
    losers = [r for r in results if isinstance(r, ConflictError)]
    assert len(winners) == 1 and len(losers) == len(moderators) - 1
    assert {e.code for e in losers} <= {
        "social.case_already_claimed",
        "social.case_version_conflict",
    }
    (row,) = await social.rows(t.moderation_cases)
    assert (row.status, row.version) == ("in_work", 2)
    assert row.assignee_id in moderators
    assert await social.count(t.moderation_audit) == 1


async def test_parallel_resolve_publishes_events_once(social: SocialService) -> None:
    case_id = await _case(social)
    cases = social.container.cases
    moderator = Staff(uuid7(), PERMS)
    await cases.claim(moderator, case_id, 1, _key())
    results = await asyncio.gather(
        *(
            cases.resolve(
                moderator,
                case_id,
                version=2,
                decision="dismissed",
                sanction=None,
                reason=REASON,
                key=_key(),
            )
            for _ in range(4)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(r, dict) for r in results) == 1
    assert all(isinstance(r, ConflictError) for r in results if not isinstance(r, dict))
    assert len(await social.outbox_events("social.ContentModerated")) == 1
    reporters = social.config.config.reports.threshold_per_day
    assert len(await social.outbox_events("social.ReportResolved")) == reporters


async def test_resolve_sanctioned_is_atomic_with_granter(social: SocialService) -> None:
    case_id = await _case(social)
    cases = social.container.cases
    moderator = Staff(uuid7(), PERMS)
    await cases.claim(moderator, case_id, 1, _key())
    social.granter.error = ConflictError("сбой выдачи", code="social.sanction_conflict")
    with pytest.raises(ConflictError):
        await cases.resolve(
            moderator,
            case_id,
            version=2,
            decision="sanctioned",
            sanction=SanctionSpec("chat", 600, "спам"),
            reason=REASON,
            key=_key(),
        )
    (row,) = await social.rows(t.moderation_cases)
    assert (row.status, row.decision, row.version) == ("in_work", None, 2)
    assert not await social.outbox_events("social.ContentModerated")
    assert await social.count(t.moderation_audit) == 1  # только claim


async def test_avatar_projection_keeps_newest_version(social: SocialService) -> None:
    asset, owner = uuid7(), uuid7()
    factory = social.container.uow_factory()
    for version, status in ((2, "rejected"), (1, "approved")):  # старое событие пришло позже
        async with factory() as unit:
            await unit.avatars.apply(AvatarRef(asset, owner, status, version))
            await unit.commit()
    (row,) = await social.rows(t.avatar_proj)
    assert (row.status, row.version) == ("rejected", 2)
