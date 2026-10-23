"""Магазин и промокоды на реальных Postgres и Redis: гонки, откат, сверка, схема (ADR 0045)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import pytest
from progression_service.application.reconcile import LedgerReconciler
from progression_service.application.shop import ShopService
from progression_service.infrastructure.db.repositories import SqlProgressionUnitOfWork
from progression_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from gv_core.ids import uuid7
from gv_testkit import apply_migrations

pytestmark = pytest.mark.integration

Service = Any  # фикстура `progression` описана в conftest.py
PARALLEL = 20


class BoomError(RuntimeError):
    """Сбой, внедрённый тестом на заданном шаге покупки."""


async def _raise(*_: object, **__: object) -> None:
    raise BoomError


def _raise_sync(*_: object, **__: object) -> None:
    raise BoomError


class _FailingTransaction:
    """Транзакция, у которой ``commit`` падает; остальное - как у настоящей."""

    def __init__(self, real: Any) -> None:
        self._real = real

    async def commit(self) -> None:
        raise BoomError

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class FailingUow(SqlProgressionUnitOfWork):
    """Настоящий UoW с отказом на одном шаге (проверка отката всей покупки)."""

    def __init__(self, *, fail: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._fail = fail

    async def __aenter__(self) -> FailingUow:
        await super().__aenter__()
        if self._fail == "tx_commit":  # события уже в outbox, но фиксация транзакции падает
            self._tx = _FailingTransaction(self._tx)
            return self
        target: dict[str, tuple[object, str, Any]] = {
            "ledger": (self.ledger, "add_currency", _raise),
            "balance": (self.balances, "save", _raise),
            "inventory": (self.inventory, "add", _raise),
            "publish": (self, "publish", _raise_sync),
        }
        owner, name, replacement = target[self._fail]
        setattr(owner, name, replacement)
        return self


def failing_service(svc: Service, fail: str) -> ShopService:
    def factory() -> FailingUow:
        return FailingUow(
            fail=fail,
            producer="progression",
            clock=svc.clock,
            sessions=svc.infra.sessions,
        )

    return ShopService(factory, svc.clock, billing_enabled=False)


async def stock(svc: Service, user: UUID, price: int = 30, balance: int = 100) -> UUID:
    await svc.publish_config()
    item = await svc.add_item(price=price)
    await svc.grant_verses(user, balance)
    return item


# ------------------------------------------------------------------ покупка
async def test_twenty_parallel_purchases_make_one_posting_and_one_item(
    progression: Service,
) -> None:
    user = uuid7()
    item = await stock(progression, user)

    responses = await asyncio.gather(*(progression.purchase(user, item) for _ in range(PARALLEL)))

    assert [r.status_code for r in responses] == [200] * PARALLEL
    assert sorted(r.json()["repeated"] for r in responses).count(False) == 1
    assert {r.json()["item"]["id"] for r in responses} == {str(item)}
    assert len(await progression.rows("currency_ledger", user, direction="debit")) == 1
    assert len(await progression.rows("inventory", user)) == 1
    assert await progression.balance(user) == 70
    assert len(await progression.outbox("progression.ItemPurchased")) == 1


async def test_parallel_purchases_of_different_items_never_overdraw(progression: Service) -> None:
    user = uuid7()
    await progression.publish_config()
    items = [await progression.add_item(f"it_{n}", price=20) for n in range(6)]
    await progression.grant_verses(user, 50)

    responses = await asyncio.gather(*(progression.purchase(user, i) for i in items))

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 200, 409, 409, 409, 409]
    assert {r.json()["code"] for r in responses if r.status_code == 409} == {
        "progression.insufficient_funds"
    }
    assert await progression.balance(user) == 10
    assert len(await progression.rows("inventory", user)) == 2


@pytest.mark.parametrize("fail", ["ledger", "balance", "inventory", "publish", "tx_commit"])
async def test_failure_at_any_step_rolls_back_the_whole_purchase(
    progression: Service, fail: str
) -> None:
    user = uuid7()
    item = await stock(progression, user)
    debits_before = await progression.outbox("progression.CurrencyDebited")

    with pytest.raises(BoomError):
        await failing_service(progression, fail).purchase(user, item)

    assert await progression.balance(user) == 100
    assert await progression.rows("currency_ledger", user, direction="debit") == []
    assert await progression.rows("inventory", user) == []
    assert await progression.outbox("progression.ItemPurchased") == []
    assert len(await progression.outbox("progression.CurrencyDebited")) == len(debits_before)
    # после сбоя обычная покупка проходит: ничего не «залипло»
    assert (await progression.purchase(user, item)).status_code == 200
    assert await progression.balance(user) == 70


async def test_reconcile_converges_after_purchases_promo_and_wheel(progression: Service) -> None:
    await progression.publish_config()
    users = [uuid7() for _ in range(3)]
    for n, user in enumerate(users):
        item = await progression.add_item(f"it_{n}", price=10 * (n + 1))
        await progression.grant_verses(user, 100)
        assert (await progression.purchase(user, item)).status_code == 200
    wheel_item = await progression.add_item("wheel_cosmetic", price=None, level_required=40)
    progression.rig_wheel("item_cosmetic")
    for _ in range(2):  # второй день - дубль, компенсация Версами
        assert (await progression.spin(users[0])).status_code == 200
        progression.clock.advance(days=1)
    assert wheel_item is not None

    mismatches = await LedgerReconciler(
        progression.container.uow_factory(), limit=100, statement_timeout_ms=30_000
    ).run()

    assert mismatches == []


async def test_balance_check_constraint_blocks_negative_amounts(progression: Service) -> None:
    user = uuid7()
    await stock(progression, user)
    assert progression.infra.sessions is not None

    with pytest.raises(IntegrityError):
        async with progression.infra.sessions() as session, session.begin():
            await session.execute(
                text("UPDATE balances SET amount = -1 WHERE user_id = :u"), {"u": user}
            )


# ------------------------------------------------------------------ промокод
async def redeem(svc: Service, user: UUID, code: str) -> httpx.Response:
    return await svc.client.post(
        "/v1/promo/redeem",
        json={"code": code},
        headers={**svc.user(user), "Idempotency-Key": f"idem-{uuid7().hex}"},
    )


async def batch(svc: Service, **patch: Any) -> list[str]:
    token = svc.issuer.issue(
        user_id=UUID(int=79), perms=("admin.access", "progression.promo.manage")
    )
    body = {
        "label": "Гонка",
        "reward": {"kind": "verses", "verses_amount": 50},
        "count": 1,
        "max_uses": 3,
        "reason": "проверка гонки",
        **patch,
    }
    response = await svc.client.post(
        "/admin/v1/promo-codes",
        json=body,
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": f"idem-{uuid7().hex}"},
    )
    assert response.status_code == 201, response.text
    codes: list[str] = response.json()["codes"]
    return codes


async def test_race_for_the_last_activations_never_exceeds_max_uses(progression: Service) -> None:
    await progression.publish_config(promo__attempts_per_ip=1000)
    (code,) = await batch(progression)
    users = [uuid7() for _ in range(PARALLEL)]

    responses = await asyncio.gather(*(redeem(progression, u, code) for u in users))

    assert sorted(r.status_code for r in responses).count(200) == 3
    assert {r.json()["code"] for r in responses if r.status_code != 200} == {
        "progression.promo_invalid"
    }
    (row,) = await progression.rows("promo_codes")
    assert row.activations == 3
    assert len(await progression.rows("promo_redemptions")) == 3
    assert sum([await progression.balance(u) for u in users]) == 150
    assert len(await progression.outbox("progression.PromoRedeemed")) == 3


async def test_parallel_redemptions_by_one_user_credit_once(progression: Service) -> None:
    await progression.publish_config(promo__attempts_per_user=1000, promo__attempts_per_ip=1000)
    (code,) = await batch(progression)
    user = uuid7()

    responses = await asyncio.gather(*(redeem(progression, user, code) for _ in range(10)))

    assert [r.status_code for r in responses] == [200] * 10
    assert sorted(r.json()["repeated"] for r in responses).count(False) == 1
    assert await progression.balance(user) == 50
    assert len(await progression.rows("currency_ledger", user)) == 1
    (row,) = await progression.rows("promo_codes")
    assert row.activations == 1


# ------------------------------------------------------------------ схема и миграции
async def _insert_item(session: Any, **patch: Any) -> None:
    values = {
        "id": uuid7(),
        "key": f"k_{uuid7().hex[-10:]}",
        "slot": "frame",
        "rarity": "rare",
        "title": "t",
        "media_kind": "static",
        "price": 1,
        "level_required": None,
        "asset_key": None,
        "active": False,
        **patch,
    }
    await session.execute(
        text(
            "INSERT INTO items (id, key, slot, rarity, title, media_kind, price, level_required,"
            " asset_key, active, created_at, updated_at) VALUES (:id, :key, :slot, :rarity,"
            " :title, :media_kind, :price, :level_required, :asset_key, :active, now(), now())"
        ),
        values,
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"price": None, "level_required": None},  # нет условия открытия
        {"price": 0},
        {"price": None, "level_required": 0},
        {"active": True, "asset_key": None},  # активный без картинки
        {"media_kind": "video"},
    ],
)
async def test_item_invariants_live_in_the_schema(
    progression: Service, patch: dict[str, Any]
) -> None:
    assert progression.infra.sessions is not None
    with pytest.raises(IntegrityError):
        async with progression.infra.sessions() as session, session.begin():
            await _insert_item(session, **patch)


async def test_item_key_is_unique_and_inventory_pk_prevents_duplicates(
    progression: Service,
) -> None:
    assert progression.infra.sessions is not None
    await progression.add_item("dup")
    with pytest.raises(IntegrityError):
        await progression.add_item("dup")
    user, item = uuid7(), await progression.add_item("other")
    sql = text(
        "INSERT INTO inventory (user_id, item_id, source, acquired_at)"
        " VALUES (:u, :i, 'admin', now())"
    )
    async with progression.infra.sessions() as session, session.begin():
        await session.execute(sql, {"u": user, "i": item})
    with pytest.raises(IntegrityError):
        async with progression.infra.sessions() as session, session.begin():
            await session.execute(sql, {"u": user, "i": item})


async def test_equipped_must_reference_an_inventory_row(progression: Service) -> None:
    assert progression.infra.sessions is not None
    item = await progression.add_item("not_owned")
    with pytest.raises(IntegrityError):
        async with progression.infra.sessions() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO equipped (user_id, slot, item_id, equipped_at)"
                    " VALUES (:u, 'frame', :i, now())"
                ),
                {"u": uuid7(), "i": item},
            )


async def test_promo_activations_check_and_redemption_pk(progression: Service) -> None:
    await progression.publish_config()
    (code,) = await batch(progression, max_uses=1)
    assert progression.infra.sessions is not None
    user = uuid7()
    assert (await redeem(progression, user, code)).status_code == 200
    (row,) = await progression.rows("promo_codes")
    with pytest.raises(IntegrityError):
        async with progression.infra.sessions() as session, session.begin():
            await session.execute(
                text("UPDATE promo_codes SET activations = 2 WHERE id = :i"), {"i": row.id}
            )
    with pytest.raises(IntegrityError):
        async with progression.infra.sessions() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO promo_redemptions (code_hash, user_id, code_id, batch_id,"
                    " redeemed_at) VALUES (:h, :u, :c, :b, now())"
                ),
                {"h": row.code_hash, "u": user, "c": row.id, "b": row.batch_id},
            )


async def _downgrade(database_url: str, revision: str) -> None:
    from alembic import command

    from gv_db.migrations import alembic_config

    assert DEFINITION.migrations_path is not None
    await asyncio.to_thread(
        command.downgrade, alembic_config(DEFINITION.migrations_path, database_url), revision
    )


async def _scalar(database_url: str, sql: str) -> Any:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql))).scalar_one()
    finally:
        await engine.dispose()


async def test_migrations_0005_0006_go_down_and_up_on_empty_data(database_url: str) -> None:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)

    await _downgrade(database_url, "0004")

    assert await _scalar(database_url, "SELECT version_num FROM alembic_version") == "0004"
    tables = await _scalar(
        database_url,
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
        " AND tablename IN ('items','inventory','equipped','packs','pack_items',"
        "'promo_batches','promo_codes','promo_redemptions')",
    )
    assert tables == 0
    await apply_migrations(database_url, DEFINITION.migrations_path)
    assert await _scalar(database_url, "SELECT count(*) FROM items") == 0


@pytest.mark.parametrize(
    ("revision", "table", "stays"),
    [("0005", "promo_codes", "0006"), ("0004", "inventory", "0005")],
)
async def test_downgrade_with_issued_data_is_refused(
    progression: Service, database_url: str, revision: str, table: str, stays: str
) -> None:
    """Выданные коды и купленное не удаляются молча (как журналы в 0002)."""
    user = uuid7()
    await progression.publish_config()
    if table == "promo_codes":
        await batch(progression)
    else:
        item = await progression.add_item(price=10)
        await progression.grant_verses(user, 10)
        assert (await progression.purchase(user, item)).status_code == 200

    with pytest.raises(Exception, match="запрещён"):
        await _downgrade(database_url, revision)

    assert await _scalar(database_url, f"SELECT count(*) FROM {table}") >= 1  # noqa: S608
    assert await _scalar(database_url, "SELECT version_num FROM alembic_version") == stays


_ = Callable  # используется в аннотациях тестов выше при необходимости
