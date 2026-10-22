"""Миграции применяются на чистую базу и совпадают с метаданными (нет «дрейфа» схемы)."""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from identity_service.infrastructure.db.tables import TABLES
from identity_service.main import DEFINITION
from sqlalchemy.ext.asyncio import create_async_engine

from gv_testkit import apply_migrations

pytestmark = pytest.mark.integration


async def test_migrations_apply_and_match_metadata(database_url: str) -> None:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            diff = await conn.run_sync(
                lambda sync: compare_metadata(MigrationContext.configure(sync), TABLES.metadata)
            )
    finally:
        await engine.dispose()
    assert diff == [], f"миграции расходятся с метаданными: {diff}"


GAME_PERMS = {
    "config.modes.view",
    "config.modes.edit",
    "config.modes.publish",
    "game.daily.view",
    "game.daily.curate",
}


async def _grants(database_url: str) -> dict[str, set[str]]:
    from sqlalchemy import text

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT role, perm FROM role_permissions"))).all()
            perms = set((await conn.execute(text("SELECT name FROM permissions"))).scalars())
    finally:
        await engine.dispose()
    grants: dict[str, set[str]] = {"*": perms}
    for role, perm in rows:
        grants.setdefault(role, set()).add(perm)
    return grants


async def test_0005_seeds_game_perms_and_is_reversible(database_url: str) -> None:
    """План этапа 2, §2.7: admin — все права, moderator — просмотр и курация."""
    import asyncio

    from alembic import command

    from gv_db.migrations import alembic_config

    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    grants = await _grants(database_url)
    assert grants["*"] >= GAME_PERMS
    assert grants["admin"] >= GAME_PERMS
    assert grants["moderator"] & GAME_PERMS == {
        "config.modes.view",
        "game.daily.view",
        "game.daily.curate",
    }
    assert not grants.get("user", set()) & GAME_PERMS

    config = alembic_config(DEFINITION.migrations_path, database_url)
    await asyncio.to_thread(command.downgrade, config, "0004")
    assert not (await _grants(database_url))["*"] & GAME_PERMS
    await asyncio.to_thread(command.upgrade, config, "head")
    assert (await _grants(database_url))["admin"] >= GAME_PERMS


COLLECTIONS_PERMS = {
    "collections.view",
    "collections.curate",
    "collections.unpublish",
    "collections.restrict_author",
}


async def test_0007_seeds_collections_perms_and_is_reversible(database_url: str) -> None:
    """План этапа 3b, §2.8: admin — все 4 права, moderator — просмотр и скрытие."""
    import asyncio

    from alembic import command

    from gv_db.migrations import alembic_config

    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    grants = await _grants(database_url)
    assert grants["*"] >= COLLECTIONS_PERMS
    assert grants["admin"] >= COLLECTIONS_PERMS
    assert grants["moderator"] & COLLECTIONS_PERMS == {"collections.view", "collections.unpublish"}
    assert not grants.get("user", set()) & COLLECTIONS_PERMS

    config = alembic_config(DEFINITION.migrations_path, database_url)
    await asyncio.to_thread(command.downgrade, config, "0006")
    after = await _grants(database_url)
    assert not after["*"] & COLLECTIONS_PERMS
    assert after["admin"] >= GAME_PERMS  # права 0005 откат не трогает
    await asyncio.to_thread(command.upgrade, config, "head")
    assert (await _grants(database_url))["admin"] >= COLLECTIONS_PERMS


ONLINE_PERMS = {
    "match.spectate",
    "match.manage",
    "rating.ladders.publish",
    "rating.seasons.manage",
}


async def test_0008_seeds_online_perms_and_is_reversible(database_url: str) -> None:
    """План этапа 4, §2.7 и D19: admin — все 4 права, moderator — только наблюдение."""
    import asyncio

    from alembic import command

    from gv_db.migrations import alembic_config

    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    grants = await _grants(database_url)
    assert grants["*"] >= ONLINE_PERMS
    assert grants["admin"] >= ONLINE_PERMS
    assert grants["moderator"] & ONLINE_PERMS == {"match.spectate"}
    assert not grants.get("user", set()) & ONLINE_PERMS

    config = alembic_config(DEFINITION.migrations_path, database_url)
    await asyncio.to_thread(command.downgrade, config, "0007")
    after = await _grants(database_url)
    assert not after["*"] & ONLINE_PERMS
    assert not after["admin"] & ONLINE_PERMS
    assert after["admin"] >= COLLECTIONS_PERMS | GAME_PERMS  # права 0005 и 0007 откат не трогает
    # Повторный upgrade идемпотентен по отношению к уже существующей матрице.
    await asyncio.to_thread(command.upgrade, config, "head")
    assert (await _grants(database_url))["moderator"] & ONLINE_PERMS == {"match.spectate"}


async def _tables(database_url: str) -> set[str]:
    from sqlalchemy import text

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
            )
            return set(rows.scalars())
    finally:
        await engine.dispose()


async def test_0009_0010_codes_replace_email_tokens_and_are_reversible(database_url: str) -> None:
    """ADR 0041: head — коды и устройства без email_tokens; откат возвращает прежнюю схему."""
    import asyncio

    from alembic import command

    from gv_db.migrations import alembic_config

    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    head = await _tables(database_url)
    assert {"email_challenges", "trusted_devices"} <= head
    assert "email_tokens" not in head

    config = alembic_config(DEFINITION.migrations_path, database_url)
    await asyncio.to_thread(command.downgrade, config, "0009")
    assert "email_tokens" in await _tables(database_url)
    await asyncio.to_thread(command.downgrade, config, "0008")
    tables = await _tables(database_url)
    assert "email_tokens" in tables
    assert not {"email_challenges", "trusted_devices"} & tables
    await asyncio.to_thread(command.upgrade, config, "head")
    assert {"email_challenges", "trusted_devices"} <= await _tables(database_url)


async def test_0011_invariants_and_cleanup_indexes_are_reversible(database_url: str) -> None:
    """Ревью ADR 0041: CHECK-инварианты и индексы очистки добавляются и откатываются."""
    import asyncio

    from alembic import command
    from sqlalchemy import text

    from gv_db.migrations import alembic_config

    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    names = (
        "ck_email_challenges_attempts_within_max",
        "ck_email_challenges_closed_once",
        "ix_email_challenges_user_id",
        "ix_trusted_devices_expires_at",
    )

    async def present() -> set[str]:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as conn:
                rows = await conn.execute(
                    text("SELECT conname FROM pg_constraint UNION SELECT indexname FROM pg_indexes")
                )
                return {row[0] for row in rows}
        finally:
            await engine.dispose()

    assert set(names) <= await present()
    config = alembic_config(DEFINITION.migrations_path, database_url)
    await asyncio.to_thread(command.downgrade, config, "0010")
    assert not set(names) & await present()
    await asyncio.to_thread(command.upgrade, config, "head")
    assert set(names) <= await present()
