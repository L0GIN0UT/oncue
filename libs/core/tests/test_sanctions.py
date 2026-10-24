"""Маска санкций: виды -> биты, истечение, объединение, разбор события."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gv_core.sanctions import (
    CHAT_MUTE_BIT,
    COMMENTS_BIT,
    ONLINE_BIT,
    RANKED_BIT,
    SanctionEntry,
    effective_mask,
    is_account_banned,
    mask_of_kinds,
    merge_masks,
    parse_entries,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
KINDS = ["ranked", "chat", "online", "account"]


def test_kinds_map_to_claim_bits() -> None:
    assert mask_of_kinds(["ranked"]) == RANKED_BIT
    assert mask_of_kinds(["chat"]) == CHAT_MUTE_BIT | COMMENTS_BIT
    assert mask_of_kinds(["online"]) == ONLINE_BIT
    assert mask_of_kinds(["account"]) == 0
    assert mask_of_kinds(["from_the_future"]) == 0


def test_never_issues_reserved_bits() -> None:
    reserved = 2 | 4  # CUSTOM_CONTENT, CHAT_SLOW - модератор не выдаёт
    assert mask_of_kinds(KINDS) & reserved == 0


def test_expired_entries_leave_the_mask_and_boundary_is_expired() -> None:
    entries = [
        SanctionEntry("online", NOW),  # граница: уже не действует
        SanctionEntry("ranked", NOW + timedelta(seconds=1)),
        SanctionEntry("chat", None),
    ]
    assert effective_mask(entries, NOW) == int(RANKED_BIT | CHAT_MUTE_BIT | COMMENTS_BIT)


def test_account_ban_respects_expiry() -> None:
    assert is_account_banned([SanctionEntry("account", None)], NOW)
    assert is_account_banned([SanctionEntry("account", NOW + timedelta(hours=1))], NOW)
    assert not is_account_banned([SanctionEntry("account", NOW)], NOW)
    assert not is_account_banned([SanctionEntry("online", None)], NOW)


def test_parse_entries_reads_event_active_list() -> None:
    entries = parse_entries(
        [
            {"kind": "online", "expires_at": "2026-10-31T12:09:00Z", "sanction_id": "x"},
            {"kind": "chat", "expires_at": None},
        ]
    )
    assert entries[0].expires_at == datetime(2026, 10, 31, 12, 9, tzinfo=UTC)
    assert entries[1] == SanctionEntry("chat", None)


@pytest.mark.parametrize(
    "bad",
    [
        [{"kind": "online"}],
        [{"kind": "online", "expires_at": "вчера"}],
        [{"kind": 1, "expires_at": None}],
    ],
)
def test_parse_entries_rejects_broken_payload(bad: list[dict[str, object]]) -> None:
    with pytest.raises((KeyError, ValueError, TypeError)):
        parse_entries(bad)  # type: ignore[arg-type]  # намеренно битые данные


def test_parse_entries_rejects_naive_time() -> None:
    with pytest.raises(ValueError):
        parse_entries([{"kind": "online", "expires_at": "2026-10-31T12:09:00"}])


@given(st.lists(st.integers(0, 63)), st.lists(st.integers(0, 63)))
def test_merge_never_drops_a_restriction(a: list[int], b: list[int]) -> None:
    merged = merge_masks(*a, *b)
    for mask in (*a, *b):
        assert merged & mask == mask


@given(st.lists(st.sampled_from(KINDS)), st.lists(st.sampled_from(KINDS)))
def test_mask_is_monotonic_in_kinds(kinds: list[str], more: list[str]) -> None:
    base = mask_of_kinds(kinds)
    assert mask_of_kinds([*kinds, *more]) & base == base
