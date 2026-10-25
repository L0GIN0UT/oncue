"""Use-case win-trading на двойнике хранилища: учёт дуэлей, пороги из конфига, очередь."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from social_fakes import _EXAMPLE, FakeConfig
from social_service.application.win_trading import WinTradingService
from social_service.domain.config import SocialConfig
from win_trading_fakes import FakeWinTradingStore

from gv_core.errors import InvalidInputError
from gv_testkit import FakeClock

pytestmark = pytest.mark.unit

A = UUID(int=1)
B = UUID(int=2)
T0 = datetime(2026, 10, 1, tzinfo=UTC)


def event(
    minute: int, winner: UUID = A, forfeiter: UUID | None = None, *, ranked: bool = True
) -> dict[str, Any]:
    loser = B if winner == A else A
    return {
        "match_id": str(uuid4()),
        "ranked": ranked,
        "ladder": "music.classic" if ranked else None,
        "aborted": False,
        "finished_at": (T0 + timedelta(minutes=minute)).isoformat(),
        "players": [
            {"user_id": str(winner), "finish": "completed", "outcome": "win"},
            {
                "user_id": str(loser),
                "finish": "forfeit" if forfeiter == loser else "completed",
                "outcome": "loss",
            },
        ],
    }


def raw_example() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    return data


def config_with(*, min_matches: int = 3, streak_min: int = 2, window_min: int = 3) -> SocialConfig:
    raw = raw_example()
    raw["win_trading"] = {
        "pair_repeat": {"window_s": 3600, "min_matches": min_matches, "strong_streak": 3},
        "forfeit_series": {"window_s": 3600, "streak_min": streak_min, "window_min": window_min},
    }
    return SocialConfig.from_body(2, raw)


def make(config: SocialConfig) -> tuple[WinTradingService, FakeWinTradingStore, FakeConfig]:
    store = FakeWinTradingStore()
    provider = FakeConfig(config)
    return WinTradingService(store.open, provider, FakeClock()), store, provider


async def test_pair_signal_appears_at_threshold_and_is_idempotent() -> None:
    service, store, _ = make(config_with(min_matches=3))
    events = [event(0), event(1, B), event(2)]
    for e in events[:2]:
        await service.record(e)
    assert store.stored == {}
    await service.record(events[2])
    await service.record(events[2])  # повтор того же матча
    (signal,) = store.stored.values()
    assert (signal.kind, signal.count) == ("pair_repeat", 3)
    assert signal.severity == "medium"


async def test_thresholds_come_from_config_and_signal_is_withdrawn() -> None:
    service, store, provider = make(config_with(min_matches=2))
    await service.record(event(0))
    await service.record(event(1))
    assert len(store.stored) == 1
    provider.config = config_with(min_matches=9)
    await service.record(event(2))
    assert store.stored == {}  # порог вырос: сигнал снят при пересчёте


async def test_non_ranked_matches_are_ignored() -> None:
    service, store, _ = make(config_with(min_matches=1))
    await service.record(event(0, ranked=False))
    assert store.facts == {}


async def test_forfeit_series_and_window_signals() -> None:
    service, store, _ = make(config_with(min_matches=99, streak_min=2, window_min=3))
    for minute in range(3):
        await service.record(event(minute, winner=B, forfeiter=A))
    assert sorted(s.kind for s in store.stored.values()) == ["forfeit_series", "forfeit_window"]
    await service.record(event(3, winner=A))  # нормальный матч обрывает серию
    assert sorted(s.kind for s in store.stored.values()) == ["forfeit_window"]


async def test_queue_filters_and_paginates() -> None:
    service, _, _ = make(config_with(min_matches=1, streak_min=1, window_min=1))
    await service.record(event(0, winner=B, forfeiter=A))
    page = await service.queue(kind=None, severity=None, user_id=None, cursor=None, limit=1)
    assert len(page["items"]) == 1
    assert page["next_cursor"] is not None
    rest = await service.queue(
        kind=None, severity=None, user_id=None, cursor=page["next_cursor"], limit=10
    )
    assert len(rest["items"]) == 2
    only_pairs = await service.queue(
        kind="pair_repeat", severity=None, user_id=B, cursor=None, limit=10
    )
    assert [i["kind"] for i in only_pairs["items"]] == ["pair_repeat"]
    with pytest.raises(InvalidInputError):
        await service.queue(kind="nope", severity=None, user_id=None, cursor=None, limit=10)


async def test_forget_user_removes_facts_and_signals() -> None:
    service, store, _ = make(config_with(min_matches=1))
    await service.record(event(0))
    await service.forget_user(A)
    assert store.facts == {} and store.stored == {}
    assert await service.for_user(A) == []


def test_absent_section_uses_defaults() -> None:
    raw = {k: v for k, v in raw_example().items() if k != "win_trading"}
    cfg = SocialConfig.from_body(1, raw).win_trading
    assert (cfg.pair_repeat.min_matches, cfg.forfeit_series.streak_min) == (5, 3)
