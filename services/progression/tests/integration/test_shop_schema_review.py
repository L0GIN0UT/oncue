"""Схема 0005/0006 после ревью: составной FK погашения, RESTRICT, индексы потомков, откат."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest
from progression_service.infrastructure.db.repositories_shop import SqlItems
from progression_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from gv_core.ids import uuid7

pytestmark = pytest.mark.integration

Service = Any  # фикстура `progression` описана в conftest.py
SHOP_TABLES = (
    "items",
    "inventory",
    "equipped",
    "packs",
    "pack_items",
    "promo_batches",
    "promo_codes",
    "promo_redemptions",
)


async def batch_codes(svc: Service, count: int = 2) -> None:
    token = svc.issuer.issue(
        user_id=UUID(int=83), perms=("admin.access", "progression.promo.manage")
    )
    response = await svc.client.post(
        "/admin/v1/promo-codes",
        json={
            "label": "Т",
            "reward": {"kind": "verses", "verses_amount": 5},
            "count": count,
            "max_uses": 3,
            "reason": "проверка схемы",
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": f"idem-{uuid7().hex}"},
    )
    assert response.status_code == 201, response.text


async def test_redemption_must_match_code_hash_and_batch_together(progression: Service) -> None:
    await progression.publish_config()
    await batch_codes(progression, 2)
    await batch_codes(progression, 1)
    sessions = progression.infra.sessions
    assert sessions is not None
    codes = await progression.rows("promo_codes")
    first = codes[0]
    second = next(c for c in codes if c.batch_id == first.batch_id and c.id != first.id)
    other_batch = next(c for c in codes if c.batch_id != first.batch_id)
    sql = text(
        "INSERT INTO promo_redemptions (code_hash, user_id, code_id, batch_id, redeemed_at)"
        " VALUES (:h, :u, :c, :b, now())"
    )

    async def insert(**values: Any) -> None:
        async with sessions() as session, session.begin():
            await session.execute(sql, {"u": uuid7(), **values})

    with pytest.raises(IntegrityError):  # хеш одного кода, id другого
        await insert(h=first.code_hash, c=second.id, b=first.batch_id)
    with pytest.raises(IntegrityError):  # код одной партии, партия другая
        await insert(h=first.code_hash, c=first.id, b=other_batch.batch_id)
    await insert(h=first.code_hash, c=first.id, b=first.batch_id)  # согласованная - проходит


async def _scalars(svc: Service, sql: str) -> list[Any]:
    assert svc.infra.sessions is not None
    async with svc.infra.sessions() as session:
        return [r[0] for r in (await session.execute(text(sql))).all()]


async def test_every_foreign_key_of_the_shop_tables_is_restrict(progression: Service) -> None:
    tables = ", ".join(f"'{t}'" for t in SHOP_TABLES)
    rows = await _scalars(
        progression,
        "SELECT confdeltype::text FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid"  # noqa: S608 - имена из константы
        f" WHERE c.contype = 'f' AND t.relname IN ({tables})",
    )
    # inventory, equipped, pack_items x2, promo_batches, promo_codes, promo_redemptions
    assert len(rows) == 7
    assert set(rows) == {"r"}


@pytest.mark.parametrize(
    "index",
    [
        "ix_pack_items_item",
        "ix_promo_batches_item",
        "ix_promo_redemptions_code",
        "ix_equipped_user_item",
    ],
)
async def test_child_side_foreign_key_indexes_exist(progression: Service, index: str) -> None:
    found = await _scalars(
        progression,
        f"SELECT indexname FROM pg_indexes WHERE indexname = '{index}'",  # noqa: S608
    )
    assert found == [index]


async def test_deleting_a_referenced_item_is_blocked(progression: Service) -> None:
    item = await progression.add_item("owned_by_someone")
    user = uuid7()
    assert progression.infra.sessions is not None
    async with progression.infra.sessions() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO inventory (user_id, item_id, source, acquired_at)"
                " VALUES (:u, :i, 'admin', now())"
            ),
            {"u": user, "i": item},
        )
    with pytest.raises(IntegrityError):
        async with progression.infra.sessions() as session, session.begin():
            await session.execute(text("DELETE FROM items WHERE id = :i"), {"i": item})


# ------------------------------------------------------------------ откат
async def _downgrade(database_url: str, revision: str) -> None:
    from alembic import command

    from gv_db.migrations import alembic_config

    assert DEFINITION.migrations_path is not None
    await asyncio.to_thread(
        command.downgrade, alembic_config(DEFINITION.migrations_path, database_url), revision
    )


async def _version(database_url: str) -> str:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            return str(
                (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            )
    finally:
        await engine.dispose()


async def test_downgrade_0005_is_refused_while_items_exist(
    progression: Service, database_url: str
) -> None:
    await progression.add_item("only_an_item")  # покупок нет, но предметы есть

    with pytest.raises(Exception, match=r"запрещён.*предмет\w*\D+1"):
        await _downgrade(database_url, "0004")

    assert await _version(database_url) == "0005"


async def test_downgrade_0006_is_refused_while_packs_exist(
    progression: Service, database_url: str
) -> None:
    await progression.publish_config()
    item = await progression.add_item("pack_member")
    token = progression.issuer.issue(
        user_id=UUID(int=84), perms=("admin.access", "progression.items.manage")
    )
    created = await progression.client.post(
        "/admin/v1/packs",
        json={
            "key": "winter",
            "title": "Зима",
            "style": "winter",
            "item_ids": [str(item)],
            "price_kopecks": 100,
            "reason": "проверка схемы",
        },
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": f"idem-{uuid7().hex}"},
    )
    assert created.status_code == 201

    with pytest.raises(Exception, match=r"запрещён.*пак\w*\D+1"):
        await _downgrade(database_url, "0005")

    assert await _version(database_url) == "0006"


async def test_for_update_read_blocks_a_concurrent_writer(progression: Service) -> None:
    item = await progression.add_item("locked_item")
    assert progression.infra.sessions is not None
    sessions = progression.infra.sessions

    async with sessions() as holder, holder.begin():
        assert await SqlItems(holder).get(item, for_update=True) is not None
        async with sessions() as other, other.begin():
            await other.execute(text("SET LOCAL lock_timeout = '300ms'"))
            with pytest.raises(DBAPIError):
                await SqlItems(other).get(item, for_update=True)
    # без FOR UPDATE чтение не блокируется
    async with sessions() as holder, holder.begin():
        await SqlItems(holder).get(item, for_update=True)
        async with sessions() as reader, reader.begin():
            assert await SqlItems(reader).get(item) is not None
