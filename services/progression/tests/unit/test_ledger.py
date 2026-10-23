"""Домен: проводки журнала, баланс не ниже нуля, сторно; серия дейлика."""

from __future__ import annotations

import contextlib
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from progression_service.domain.daily_streak import Streak
from progression_service.domain.ledger import (
    AlreadyReversedError,
    Bucket,
    Direction,
    Entry,
    InsufficientBalanceError,
    NotReversibleError,
    apply_to_balance,
    credit,
    debit,
    reversal,
)

from gv_core.errors import DomainError

pytestmark = pytest.mark.unit

USER = UUID(int=7)
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def entry(
    amount: int = 10, *, op_id: str = "op-1", direction: Direction = Direction.CREDIT
) -> Entry:
    make = credit if direction is Direction.CREDIT else debit
    return make(
        op_id=op_id,
        user_id=USER,
        amount=amount,
        reason="solo_round",
        source_ref="ref",
        config_version=1,
        at=NOW,
    )


def test_credit_and_debit_change_balance_by_sign() -> None:
    assert apply_to_balance(5, entry(10)) == 15
    assert apply_to_balance(15, entry(10, direction=Direction.DEBIT)) == 5


def test_balance_cannot_go_below_zero() -> None:
    with pytest.raises(InsufficientBalanceError) as exc:
        apply_to_balance(5, entry(6, direction=Direction.DEBIT))
    assert exc.value.code == "progression.insufficient_balance"
    assert apply_to_balance(5, entry(5, direction=Direction.DEBIT)) == 0


@pytest.mark.parametrize("amount", [0, -1])
def test_amount_must_be_positive(amount: int) -> None:
    with pytest.raises(DomainError):
        entry(amount)


def test_reversal_is_opposite_entry_with_link() -> None:
    original = entry(10)
    rev = reversal(
        original,
        op_id="rev-1",
        actor_id=UUID(int=1),
        reason="admin_reverse",
        at=NOW,
        config_version=2,
        already_reversed=False,
    )
    assert (rev.direction, rev.amount, rev.bucket) == (Direction.DEBIT, 10, Bucket.EARNED)
    assert rev.reversal_of == "op-1"
    assert rev.actor_id == UUID(int=1)
    assert apply_to_balance(10, rev) == 0


def test_reversal_cannot_be_repeated_or_reversed() -> None:
    original = entry(10)
    kwargs = {"actor_id": UUID(int=1), "reason": "r", "at": NOW, "config_version": 1}
    with pytest.raises(AlreadyReversedError) as exc:
        reversal(original, op_id="rev-2", already_reversed=True, **kwargs)  # type: ignore[arg-type]
    assert exc.value.code == "progression.already_reversed"
    rev = reversal(original, op_id="rev-1", already_reversed=False, **kwargs)  # type: ignore[arg-type]
    with pytest.raises(NotReversibleError):
        reversal(rev, op_id="rev-3", already_reversed=False, **kwargs)  # type: ignore[arg-type]


@given(ops=st.lists(st.tuples(st.booleans(), st.integers(1, 100)), max_size=40))
def test_balance_never_negative_over_any_sequence(ops: list[tuple[bool, int]]) -> None:
    balance = 0
    for is_credit, amount in ops:
        e = entry(amount, direction=Direction.CREDIT if is_credit else Direction.DEBIT)
        with contextlib.suppress(InsufficientBalanceError):
            balance = apply_to_balance(balance, e)
        assert balance >= 0


# ------------------------------------------------------------------ серия дейлика
D1 = date(2026, 10, 1)


def test_streak_grows_on_consecutive_days_and_resets_after_gap() -> None:
    s, changed = Streak().advance(D1)
    assert (s.current, s.best, changed) == (1, 1, True)
    s, _ = s.advance(date(2026, 10, 2))
    s, _ = s.advance(date(2026, 10, 3))
    assert (s.current, s.best) == (3, 3)
    s, changed = s.advance(date(2026, 10, 5))  # пропуск 4 октября
    assert (s.current, s.best, changed) == (1, 3, True)


def test_streak_repeat_and_late_event_do_not_change_anything() -> None:
    s, _ = Streak().advance(D1)
    s, _ = s.advance(date(2026, 10, 2))
    same, changed = s.advance(date(2026, 10, 2))
    assert (same, changed) == (s, False)
    late, changed = s.advance(D1)
    assert (late, changed) == (s, False)


def test_streak_month_boundary() -> None:
    s, _ = Streak().advance(date(2026, 10, 31))
    s, _ = s.advance(date(2026, 11, 1))
    assert s.current == 2
