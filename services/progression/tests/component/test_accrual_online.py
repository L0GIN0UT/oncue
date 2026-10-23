"""Начисление за онлайн-матчи и награды рейтинга: идемпотентность по предмету (P1)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from gv_core.ids import uuid7
from gv_messaging import Outcome
from gv_testkit import assert_event_matches_schema

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py


async def test_duel_rewards_participation_rounds_win_and_first_win(progression: Service) -> None:
    await progression.publish_config()
    winner, loser = uuid7(), uuid7()
    assert await progression.deliver(progression.duel(winner, loser)) is Outcome.PROCESSED

    # победитель: 40 + 10·2 раунда + 60 победа, плюс 200 за первую победу дня
    assert await progression.xp_total(winner) == 120 + 200
    assert await progression.balance(winner) == 10
    # проигравший: 40 + 10·1 раунд, 3 Верса
    assert await progression.xp_total(loser) == 50
    assert await progression.balance(loser) == 3

    sources = {
        e.payload["source"] for e in await progression.outbox("progression.ExperienceGained")
    }
    assert sources == {"duel", "first_win"}
    for event in await progression.outbox():
        assert_event_matches_schema(event)


async def test_same_match_twice_and_with_other_event_id_is_one_posting(
    progression: Service,
) -> None:
    await progression.publish_config()
    winner, loser = uuid7(), uuid7()
    match_id = uuid7()
    first = progression.duel(winner, loser, match_id=match_id)
    assert await progression.deliver(first) is Outcome.PROCESSED
    assert await progression.deliver(first) is Outcome.DUPLICATE
    replay = progression.duel(winner, loser, match_id=match_id)  # новый event_id
    assert replay.event_id != first.event_id
    assert await progression.deliver(replay) is Outcome.PROCESSED

    assert await progression.xp_rows() == 3  # победитель: матч + первая победа; проигравший: матч
    assert await progression.verse_rows() == 2
    assert await progression.xp_total(winner) == 320
    assert await progression.balance(winner) == 10


async def test_first_win_of_day_is_given_once_per_local_day(progression: Service) -> None:
    await progression.publish_config()
    winner, loser = uuid7(), uuid7()
    await progression.deliver(progression.duel(winner, loser))
    await progression.deliver(progression.duel(winner, loser))
    assert await progression.xp_total(winner) == 120 + 200 + 120

    next_day = progression.clock.now() + timedelta(days=1)
    await progression.deliver(progression.duel(winner, loser, finished_at=next_day))
    assert await progression.xp_total(winner) == 120 + 200 + 120 + 120 + 200


async def test_parallel_matches_cannot_double_the_first_win_bonus(progression: Service) -> None:
    await progression.publish_config()
    winner = uuid7()
    events = [progression.duel(winner, uuid7()) for _ in range(6)]
    await asyncio.gather(*(progression.deliver(e) for e in events))
    assert await progression.xp_total(winner) == 6 * 120 + 200


async def test_forfeit_leave_and_aborted_give_nothing(progression: Service) -> None:
    await progression.publish_config()
    a, b, c = uuid7(), uuid7(), uuid7()
    rounds = [[(a, 10), (b, 0)], [(a, 10), (b, 0)]]
    await progression.deliver(
        progression.match_finished(
            [
                {"user_id": a, "place": 1, "outcome": "win"},
                {"user_id": b, "place": 2, "outcome": "loss", "finish": "forfeit"},
            ],
            rounds=rounds,
        )
    )
    await progression.deliver(progression.duel(c, uuid7(), aborted=True))
    assert await progression.xp_total(b) == 0
    assert await progression.verse_rows(b) == 0
    assert await progression.xp_total(c) == 0
    assert await progression.verse_rows(c) == 0


async def test_tech_win_gets_participation_without_win_bonus(progression: Service) -> None:
    await progression.publish_config()
    a, b = uuid7(), uuid7()
    await progression.deliver(
        progression.match_finished(
            [
                {"user_id": a, "place": 1, "outcome": "win", "finish": "tech_win"},
                {"user_id": b, "place": 2, "outcome": "loss", "finish": "tech_loss"},
            ],
            rounds=[[(a, 10), (b, 0)]],
        )
    )
    assert await progression.xp_total(a) == 40 + 10  # без +60 и без первой победы дня
    assert await progression.xp_total(b) == 0


async def test_win_bonuses_need_min_rounds_ratio(progression: Service) -> None:
    await progression.publish_config()
    a, b = uuid7(), uuid7()
    # Победитель сыграл 1 раунд из 4 (< 0.5): без бонуса за победу и первой победы дня.
    rounds = [[(a, 10), (b, 0)], [(b, 10)], [(b, 10)], [(b, 10)]]
    await progression.deliver(
        progression.match_finished(
            [
                {"user_id": a, "place": 1, "outcome": "win"},
                {"user_id": b, "place": 2, "outcome": "loss"},
            ],
            rounds=rounds,
        )
    )
    assert await progression.xp_total(a) == 40 + 10
    assert await progression.balance(a) == 3


async def test_draw_is_not_a_first_win(progression: Service) -> None:
    await progression.publish_config()
    a, b = uuid7(), uuid7()
    await progression.deliver(
        progression.match_finished(
            [
                {"user_id": a, "place": 1, "outcome": "draw"},
                {"user_id": b, "place": 1, "outcome": "draw"},
            ],
            rounds=[[(a, 10), (b, 0)], [(a, 0), (b, 10)]],
        )
    )
    assert await progression.xp_total(a) == 40 + 10
    assert sorted(
        e.payload["source"] for e in await progression.outbox("progression.ExperienceGained")
    ) == [
        "duel",
        "duel",
    ]


async def test_lobby_pays_by_place_and_party_grid(progression: Service) -> None:
    await progression.publish_config()
    users = [uuid7() for _ in range(4)]
    await progression.deliver(
        progression.match_finished(
            [{"user_id": u, "place": i + 1} for i, u in enumerate(users)],
            format="lobby",
            rounds=[[(u, 10 - i) for i, u in enumerate(users)]],
        )
    )
    assert [await progression.balance(u) for u in users] == [10, 6, 4, 2]
    assert await progression.xp_total(users[0]) == 40 + 80 + 200
    assert await progression.xp_total(users[1]) == 40 + 50
    assert await progression.xp_total(users[3]) == 40 + 10


async def test_duplicate_player_in_match_goes_to_dlq(progression: Service) -> None:
    await progression.publish_config()
    a = uuid7()
    event = progression.match_finished(
        [
            {"user_id": a, "place": 1, "outcome": "win"},
            {"user_id": a, "place": 2, "outcome": "loss"},
        ]
    )
    assert await progression.deliver(event) is Outcome.DEAD
    assert progression.dead_letters.letters[-1]["reason"] == "permanent"
    assert await progression.xp_rows() == 0


async def test_reward_hook_multiplies_online_xp_only(progression: Service) -> None:
    from decimal import Decimal

    class Boost:
        async def online_xp_multiplier(self, user_id: UUID, match_id: UUID) -> Decimal:
            return Decimal("1.5")

    progression.container.reward_hook = Boost()
    await progression.publish_config()
    a, b = uuid7(), uuid7()
    await progression.deliver(progression.duel(a, b))
    gained = {
        (e.payload["user_id"], e.payload["source"]): e.payload
        for e in await progression.outbox("progression.ExperienceGained")
    }
    duel = gained[(str(a), "duel")]
    assert (duel["base_amount"], duel["amount"], duel["boost_multiplier"]) == (120, 180, 1.5)
    # Соло буст не затрагивает (R3).
    solo_user = uuid7()
    await progression.deliver(progression.game_finished(solo_user, [100]))
    assert await progression.xp_total(solo_user) == 30


# ------------------------------------------------------------------ награды рейтинга
async def test_weekly_top_rewards_by_grid_once_per_period(progression: Service) -> None:
    await progression.publish_config()
    u1, u2, u3 = uuid7(), uuid7(), uuid7()
    top = [(u1, 1), (u2, 2), (u3, 11)]
    assert await progression.deliver(progression.weekly_top(top)) is Outcome.PROCESSED
    assert [await progression.balance(u) for u in (u1, u2, u3)] == [500, 300, 30]

    # Повтор недели другим событием (новый event_id) ничего не добавляет.
    assert await progression.deliver(progression.weekly_top(top)) is Outcome.PROCESSED
    assert [await progression.balance(u) for u in (u1, u2, u3)] == [500, 300, 30]
    assert await progression.verse_rows() == 3

    # Другая неделя и другой ladder — отдельные периоды.
    await progression.deliver(progression.weekly_top([(u1, 1)], ladder="film"))
    assert await progression.balance(u1) == 1000
    reasons = {
        e.payload["reason"] for e in await progression.outbox("progression.CurrencyCredited")
    }
    assert reasons == {"weekly_top"}


async def test_season_chunks_are_independent_and_idempotent_in_any_order(
    progression: Service,
) -> None:
    await progression.publish_config()
    season = uuid7()
    first, tenth, last, outside = uuid7(), uuid7(), uuid7(), uuid7()
    chunk0 = progression.season_standings([(first, 1)], season_id=season, chunk=0, chunks_total=3)
    chunk1 = progression.season_standings([(tenth, 10)], season_id=season, chunk=1, chunks_total=3)
    chunk2 = progression.season_standings(
        [(last, 1000), (outside, 1001)], season_id=season, chunk=2, chunks_total=3
    )
    for event in (chunk2, chunk0, chunk1):  # без порядка и без ожидания остальных пачек
        assert await progression.deliver(event) is Outcome.PROCESSED
    assert [await progression.balance(u) for u in (first, tenth, last, outside)] == [
        2000,
        400,
        50,
        0,
    ]
    # Повтор пачки и пересланная пачка с новым event_id.
    assert await progression.deliver(chunk0) is Outcome.DUPLICATE
    await progression.deliver(
        progression.season_standings([(first, 1)], season_id=season, chunk=0, chunks_total=3)
    )
    assert await progression.balance(first) == 2000
    assert await progression.verse_rows() == 3


async def test_negative_balance_is_impossible_through_the_poster(progression: Service) -> None:
    from progression_service.application.accrual.posting import Poster
    from progression_service.domain.ledger import InsufficientBalanceError, debit

    await progression.publish_config()
    user = uuid7()
    await progression.deliver(progression.weekly_top([(user, 4)]))  # +100
    async with progression.container.uow_factory()() as uow:
        published = await uow.configs.published()
        assert published is not None
        poster = Poster(uow, published, progression.clock.now())
        too_much = debit(
            op_id="spend-1",
            user_id=user,
            amount=101,
            reason="shop",
            source_ref="x",
            config_version=1,
            at=progression.clock.now(),
        )
        with pytest.raises(InsufficientBalanceError):
            await poster.post_currency(too_much)
    assert await progression.balance(user) == 100
    assert await progression.verse_rows(user) == 1


async def test_match_without_rounds_gives_participation_only(progression: Service) -> None:
    await progression.publish_config()
    a, b = uuid7(), uuid7()
    await progression.deliver(
        progression.match_finished(
            [
                {"user_id": a, "place": 1, "outcome": "win"},
                {"user_id": b, "place": 2, "outcome": "loss"},
            ],
            rounds=[],
        )
    )
    # Без бонуса победы (+60) и без первой победы дня (+200); Версы — как за поражение.
    assert await progression.xp_total(a) == 40
    assert await progression.balance(a) == 3
    assert await progression.xp_total(b) == 40
    sources = {
        e.payload["source"] for e in await progression.outbox("progression.ExperienceGained")
    }
    assert sources == {"duel"}


async def test_opposite_player_order_matches_do_not_deadlock(progression: Service) -> None:
    """Матчи (A, B) и (B, A) параллельно + награды рейтинга: единый порядок блокировок."""
    await progression.publish_config()
    for _ in range(12):
        a, b = sorted((uuid7(), uuid7()))
        season = uuid7()
        events = [
            progression.duel(a, b),  # игроки в событии: a, b
            progression.duel(b, a),  # игроки в событии: b, a
            progression.weekly_top([(b, 1), (a, 2)]),
            progression.season_standings([(b, 1), (a, 2)], season_id=season),
        ]
        async with asyncio.timeout(30):
            outcomes = await asyncio.gather(*(progression.deliver(e) for e in events))
        assert outcomes == [Outcome.PROCESSED] * 4
        # Каждый выиграл по матчу: 10 + 3 Версов; плюс сетки (неделя: 500/300, сезон: 2000/1200).
        assert await progression.balance(a) == 13 + 300 + 1200
        assert await progression.balance(b) == 13 + 500 + 2000
        # Первая победа дня выдана каждому ровно один раз: 120 + 50 + 200 опыта.
        assert await progression.xp_total(a) == 370
        assert await progression.xp_total(b) == 370
        assert await progression.verse_rows(a) == 4


@pytest.mark.parametrize(
    ("hook_value", "expected_multiplier"),
    [("1000", 10.0), ("0", 1.0), ("-5", 1.0), ("1.5", 1.5)],
)
async def test_foreign_reward_hook_multiplier_is_clamped_by_config_maximum(
    progression: Service, hook_value: str, expected_multiplier: float
) -> None:
    from decimal import Decimal

    class Hook:
        async def online_xp_multiplier(self, user_id: UUID, match_id: UUID) -> Decimal:
            return Decimal(hook_value)

    progression.container.reward_hook = Hook()
    await progression.publish_config()
    a, b = uuid7(), uuid7()
    assert await progression.deliver(progression.duel(a, b)) is Outcome.PROCESSED
    duel = next(
        e.payload
        for e in await progression.outbox("progression.ExperienceGained")
        if e.payload["user_id"] == str(a) and e.payload["source"] == "duel"
    )
    assert duel["boost_multiplier"] == expected_multiplier
    assert duel["amount"] == int(120 * expected_multiplier)
