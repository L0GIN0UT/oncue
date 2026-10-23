"""Инварианты схемы: журналы append-only, баланс не ниже нуля, UNIQUE(op_id), одно сторно."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from progression_service.application.ports import XpEntry
from progression_service.domain.ledger import Direction, credit, reversal
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from gv_core.ids import uuid7

pytestmark = pytest.mark.integration

Service = Any  # фикстура `progression` описана в conftest.py


async def _seed(progression: Service) -> UUID:
    """Игрок с одной проводкой Версов и одной проводкой опыта."""
    await progression.publish_config()
    user = uuid7()
    await progression.deliver(progression.game_finished(user, [100, 100]))
    assert await progression.verse_rows(user) == 1
    assert await progression.xp_rows(user) == 1
    return user


async def _exec(progression: Service, sql: str, **params: Any) -> None:
    async with progression.infra.sessions() as session, session.begin():
        await session.execute(text(sql), params)


@pytest.mark.parametrize("table", ["xp_ledger", "currency_ledger"])
async def test_ledger_rejects_update_delete_and_truncate(progression: Service, table: str) -> None:
    await _seed(progression)
    for sql in (
        f"UPDATE {table} SET amount = amount + 1",  # noqa: S608 — имя таблицы из parametrize
        f"UPDATE {table} SET user_id = gen_random_uuid()",  # noqa: S608
        f"DELETE FROM {table}",  # noqa: S608
        f"TRUNCATE {table}",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            await _exec(progression, sql)
    assert await progression.xp_rows() == 1
    assert await progression.verse_rows() == 1


async def test_balance_check_rejects_negative_amount(progression: Service) -> None:
    user = await _seed(progression)
    with pytest.raises(IntegrityError):
        await _exec(progression, "UPDATE balances SET amount = -1 WHERE user_id = :u", u=user)
    assert await progression.balance(user) == 2


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO currency_ledger (id, op_id, user_id, direction, amount, bucket, reason, "
        "source_ref, config_version, at) VALUES (gen_random_uuid(), 'x1', gen_random_uuid(), "
        "'credit', 0, 'earned', 'r', 's', 1, now())",
        "INSERT INTO currency_ledger (id, op_id, user_id, direction, amount, bucket, reason, "
        "source_ref, config_version, at) VALUES (gen_random_uuid(), 'x2', gen_random_uuid(), "
        "'credit', 5, 'gold', 'r', 's', 1, now())",
        "INSERT INTO currency_ledger (id, op_id, user_id, direction, amount, bucket, reason, "
        "source_ref, config_version, at) VALUES (gen_random_uuid(), 'x3', gen_random_uuid(), "
        "'credit', 5, 'earned', 'r', 's', 99, now())",
    ],
    ids=["zero_amount", "unknown_bucket", "unknown_config_version"],
)
async def test_ledger_row_invariants_are_enforced_by_schema(progression: Service, sql: str) -> None:
    await progression.publish_config()
    with pytest.raises(IntegrityError):
        await _exec(progression, sql)


async def test_op_id_is_unique_in_both_ledgers(progression: Service) -> None:
    user = await _seed(progression)
    async with progression.container.uow_factory()() as uow:
        published = await uow.configs.published()
        assert published is not None
        entry = XpEntry(
            op_id=f"game.session:dup:{user}",
            user_id=user,
            amount=5,
            base_amount=5,
            boost_multiplier=Decimal(1),
            reason="solo_round",
            source_ref="r",
            config_version=published.version,
            at=progression.clock.now(),
        )
        assert await uow.ledger.add_xp(entry) is True
        assert await uow.ledger.add_xp(entry) is False
        verse = credit(
            op_id=f"v:{user}",
            user_id=user,
            amount=3,
            reason="solo_round",
            source_ref="r",
            config_version=published.version,
            at=progression.clock.now(),
        )
        assert await uow.ledger.add_currency(verse) is True
        assert await uow.ledger.add_currency(verse) is False
        assert await uow.ledger.has_currency(verse.op_id)
        assert not await uow.ledger.has_currency("nope")
        await uow.commit()


async def test_only_one_reversal_per_entry(progression: Service) -> None:
    user = await _seed(progression)
    async with progression.container.uow_factory()() as uow:
        original = (await uow.ledger.recent_currency(user, 1))[0]
        assert original.direction is Direction.CREDIT
        kwargs = {
            "actor_id": uuid7(),
            "reason": "admin_reverse",
            "at": progression.clock.now(),
            "config_version": original.config_version,
        }
        assert not await uow.ledger.is_reversed(original.op_id)
        first = reversal(original, op_id="rev-a", already_reversed=False, **kwargs)  # type: ignore[arg-type]
        assert await uow.ledger.add_currency(first) is True
        assert await uow.ledger.is_reversed(original.op_id)
        # Второе сторно с другим op_id в обход прикладной проверки — отсекает UNIQUE(reversal_of).
        second = reversal(original, op_id="rev-b", already_reversed=False, **kwargs)  # type: ignore[arg-type]
        with pytest.raises(IntegrityError):
            await uow.ledger.add_currency(second)


async def test_only_one_published_config_version(progression: Service) -> None:
    await progression.publish_config()
    with pytest.raises(IntegrityError):
        await _exec(
            progression,
            "INSERT INTO config_versions (version, status, body, created_by, created_at, "
            "published_at) VALUES (2, 'published', '{}', gen_random_uuid(), now(), now())",
        )


async def test_reconcile_transaction_is_read_only_with_statement_timeout(
    progression: Service,
) -> None:
    """Сверка не может ничего записать и не держит долгий запрос (настройка из settings)."""
    from progression_service.settings import ProgressionSettings

    settings = ProgressionSettings()
    assert 100 <= settings.reconcile_statement_timeout_ms <= 600_000  # безопасное по умолчанию
    user = await _seed(progression)

    async with progression.container.uow_factory()() as uow:
        await uow.read_only(statement_timeout_ms=1234)
        session = uow._session
        assert session is not None
        assert (await session.execute(text("SHOW transaction_read_only"))).scalar_one() == "on"
        assert (await session.execute(text("SHOW statement_timeout"))).scalar_one() == "1234ms"
        with pytest.raises(DBAPIError):
            await session.execute(
                text("UPDATE balances SET amount = 0 WHERE user_id = :u"), {"u": user}
            )


async def test_reconciler_uses_read_only_transaction_and_still_finds_mismatches(
    progression: Service,
) -> None:
    from progression_service.application.reconcile import LedgerReconciler

    user = await _seed(progression)
    await _exec(progression, "UPDATE balances SET amount = amount + 5 WHERE user_id = :u", u=user)
    reconciler = LedgerReconciler(
        progression.container.uow_factory(), limit=10, statement_timeout_ms=5000
    )
    found = await reconciler.run()
    assert [(m.kind, m.stored - m.ledger_sum) for m in found] == [("currency", 5)]
