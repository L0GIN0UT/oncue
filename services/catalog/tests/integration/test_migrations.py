"""Миграции применяются на чистую базу и совпадают с метаданными (нет «дрейфа» схемы)."""

from __future__ import annotations

import asyncio

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from catalog_service.infrastructure.db.tables import TABLES, admin_actions, idempotency_keys
from catalog_service.main import DEFINITION
from sqlalchemy.ext.asyncio import create_async_engine

from gv_core.ids import uuid7
from gv_db.idempotency import SqlIdempotencyStore, request_fingerprint, run_idempotent
from gv_db.migrations import run_migrations
from gv_testkit import FakeClock, apply_migrations

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


async def test_idempotency_keys_backfilled_from_admin_actions(database_url: str) -> None:
    """Ключ, выданный до 0003 (ответ в ``admin_actions``), после миграции даёт тот же ответ."""
    assert DEFINITION.migrations_path is not None
    await asyncio.to_thread(run_migrations, DEFINITION.migrations_path, database_url, "0002")
    actor, target = uuid7(), uuid7()
    body = {"reason_code": "moderation", "reason": "до миграции"}
    fingerprint = request_fingerprint("item.block", target, body)
    response = {"id": str(target), "status": "blocked"}
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                admin_actions.insert().values(
                    id=uuid7(),
                    actor_id=actor,
                    idempotency_key="k-before-0003",
                    action="item.block",
                    target_id=target,
                    reason=body["reason"],
                    request_hash=fingerprint,
                    response=response,
                )
            )
        await apply_migrations(database_url, DEFINITION.migrations_path)

        async def perform() -> dict[str, object]:
            raise AssertionError("повтор не должен выполнять мутацию")

        async with engine.begin() as conn:
            store = SqlIdempotencyStore(conn, idempotency_keys, FakeClock())
            replay = await run_idempotent(store, actor, "k-before-0003", fingerprint, perform)
    finally:
        await engine.dispose()
    assert replay == (response, False)
