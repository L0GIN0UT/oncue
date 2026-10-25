"""Win-trading: события match -> сигналы -> карточка и очередь модератора (ADR 0068)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from social_fakes import _EXAMPLE
from social_service.domain.config import SocialConfig
from social_service.infrastructure.db import tables_win_trading as w

from gv_core.ids import uuid7
from gv_messaging import Outcome
from gv_testkit import assert_problem

if TYPE_CHECKING:
    from conftest import SocialService

pytestmark = pytest.mark.component

MODERATE = ("admin.access", "social.moderate")
A = "0192a3b4-c5d6-7e8f-9a0b-000000000002"
B = "0192a3b4-c5d6-7e8f-9a0b-000000000003"
T0 = datetime(2026, 10, 22, 12, tzinfo=UTC)


def _config(min_matches: int = 3) -> SocialConfig:
    raw: dict[str, Any] = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    raw["win_trading"] = {
        "pair_repeat": {"window_s": 86400, "min_matches": min_matches, "strong_streak": 3},
        "forfeit_series": {"window_s": 86400, "streak_min": 2, "window_min": 99},
    }
    return SocialConfig.from_body(2, raw)


async def _finish(
    social: SocialService, minute: int, *, ranked: bool = True, forfeit: bool = False
):
    players = [
        {"finish": "completed", "outcome": "win", "place": 1, "score": 9, "slot": 0, "user_id": A},
        {
            "finish": "forfeit" if forfeit else "completed",
            "outcome": "loss",
            "place": 2,
            "score": 1,
            "slot": 1,
            "user_id": B,
        },
    ]
    return await social.deliver(
        social.event(
            "match.MatchFinished",
            match_id=str(uuid7()),
            ranked=ranked,
            ladder="music.classic" if ranked else None,
            finished_at=(T0 + timedelta(minutes=minute)).isoformat(),
            players=players,
        )
    )


async def test_repeated_pair_becomes_signal_visible_to_moderator(social: SocialService) -> None:
    social.config.config = _config(min_matches=3)
    for minute in range(2):
        assert await _finish(social, minute) is Outcome.PROCESSED
    assert await social.count(w.win_trading_signals) == 0
    assert await _finish(social, 2) is Outcome.PROCESSED
    (row,) = await social.rows(w.win_trading_signals)
    assert (row.kind, row.match_count, row.streak, row.severity) == ("pair_repeat", 3, 3, "high")

    moderator = uuid7()
    queue = await social.client.get(
        "/admin/v1/win-trading/signals", headers=social.auth(moderator, MODERATE)
    )
    assert queue.status_code == 200
    assert queue.headers["cache-control"] == "no-store"
    (item,) = queue.json()["items"]
    assert (item["kind"], item["count"], item["window_s"]) == ("pair_repeat", 3, 86400)
    assert item["details"]["wins"] == {A: 3, B: 0}
    by_user = await social.client.get(
        "/admin/v1/win-trading/signals",
        params={"user_id": B, "kind": "forfeit_series"},
        headers=social.auth(moderator, MODERATE),
    )
    assert by_user.json()["items"] == []

    card = await social.client.get(
        f"/admin/v1/users/{A}/moderation", headers=social.auth(moderator, MODERATE)
    )
    assert card.status_code == 200
    assert [s["kind"] for s in card.json()["win_trading"]] == ["pair_repeat"]


async def test_forfeit_series_signal_and_no_automatic_effects(social: SocialService) -> None:
    social.config.config = _config(min_matches=99)
    for minute in range(2):
        await _finish(social, minute, forfeit=True)
    (row,) = await social.rows(w.win_trading_signals)
    assert (row.kind, row.user_id, row.streak) == ("forfeit_series", UUID(B), 2)
    # Детектор только показывает: ни санкций, ни событий социала.
    assert await social.outbox_events("social.UserSanctionsChanged") == []


async def test_non_ranked_match_and_duplicate_do_not_count(social: SocialService) -> None:
    social.config.config = _config(min_matches=1)
    await _finish(social, 0, ranked=False)
    assert await social.count(w.win_trading_matches) == 0
    envelope = social.event(
        "match.MatchFinished", ranked=True, ladder="music.classic", match_id=str(uuid7())
    )
    await social.deliver(envelope)
    await social.deliver(social.event("match.MatchFinished", **envelope.payload))
    assert await social.count(w.win_trading_matches) == 1


async def test_user_deleted_wipes_facts_and_signals(social: SocialService) -> None:
    social.config.config = _config(min_matches=1)
    await _finish(social, 0)
    assert await social.count(w.win_trading_signals) == 1
    await social.deliver(social.event("identity.UserDeleted", user_id=A))
    assert await social.count(w.win_trading_matches) == 0
    assert await social.count(w.win_trading_signals) == 0


async def test_queue_requires_moderate_permission(social: SocialService) -> None:
    anonymous = await social.client.get("/admin/v1/win-trading/signals")
    assert anonymous.status_code == 401
    player = await social.client.get("/admin/v1/win-trading/signals", headers=social.auth(uuid7()))
    assert_problem(player, 403, "auth.forbidden")
    bad = await social.client.get(
        "/admin/v1/win-trading/signals",
        params={"cursor": "!!"},
        headers=social.auth(uuid7(), MODERATE),
    )
    assert bad.status_code == 422
