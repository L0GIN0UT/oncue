"""Онлайн-потолок Версов в сутки (`verses.online_daily_cap`, ADR 0042): домен и разбор конфига."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from progression_service.domain import wallet
from progression_service.domain.config import ConfigInvalidError, ProgressionConfig

pytestmark = pytest.mark.unit


def _verses(make_config: Callable[..., dict[str, Any]], **patch: Any) -> Any:
    return ProgressionConfig.parse(make_config(**patch)).verses


def test_example_has_owner_cap(make_config: Callable[..., dict[str, Any]]) -> None:
    assert _verses(make_config).online_daily_cap == 500


def test_absent_cap_means_no_cap(make_config: Callable[..., dict[str, Any]]) -> None:
    body = make_config()
    del body["verses"]["online_daily_cap"]
    verses = ProgressionConfig.parse(body).verses
    assert verses.online_daily_cap is None
    assert wallet.cap_online_verses(verses, 10**9, 10**9) == 10**9


def test_zero_cap_pays_nothing(make_config: Callable[..., dict[str, Any]]) -> None:
    verses = _verses(make_config, verses__online_daily_cap=0)
    assert wallet.cap_online_verses(verses, 10, 0) == 0


@pytest.mark.parametrize("bad", [-1, 1.5, True, "5", 100_000_001])
def test_invalid_cap_is_rejected(make_config: Callable[..., dict[str, Any]], bad: Any) -> None:
    with pytest.raises(ConfigInvalidError):
        ProgressionConfig.parse(make_config(verses__online_daily_cap=bad))


@given(
    cap=st.integers(min_value=0, max_value=2000),
    matches=st.lists(st.integers(min_value=0, max_value=400), max_size=40),
)
def test_daily_sum_never_exceeds_cap_for_any_slicing(cap: int, matches: list[int]) -> None:
    """Любая нарезка и порядок матчей: сумма за сутки ≤ потолка, и потолок добирается."""
    from progression_service.domain.config import VersesConfig

    verses = _make_verses(cap)
    used = 0
    for raw in matches:
        paid = wallet.cap_online_verses(verses, raw, used)
        assert 0 <= paid <= raw
        used += paid
        assert used <= cap
    assert used == min(cap, sum(matches))
    assert isinstance(verses, VersesConfig)


def _make_verses(cap: int) -> Any:
    from progression_service.domain.config import VersesConfig

    return VersesConfig(
        solo_round=0,
        daily=0,
        duel=None,  # type: ignore[arg-type]  # не используется функцией потолка
        party_by_place=(),
        solo_daily_cap=0,
        online_daily_cap=cap,
        weekly_top=(),
        season=(),
    )
