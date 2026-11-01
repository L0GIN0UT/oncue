"""Защита от накрутки через пользовательские сборники (ugc_guard), зона повышенной проверки.

Сценарии: владелец проходит свой сборник, повтор в окне и после него, два аккаунта подряд
(суточный потолок UGC-опыта), матч по сборнику, сборник с неизвестным владельцем, повтор
события после окончания окна, одновременные события одного игрока.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from gv_core.ids import uuid7
from gv_messaging import Outcome

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py
XP_PER_ROUND = 30  # 20 + 100 очков // 10 по сидовому конфигу примера контракта


async def publish_ugc(progression: Service, author: UUID, *, kind: str = "ugc") -> UUID:
    """Сборник известен progression: событие ``CollectionPublished`` обработано."""
    collection_id = uuid7()
    event = progression.collection_published(
        None if kind == "curated" else author, collection_id=collection_id, kind=kind
    )
    assert await progression.deliver(event) is Outcome.PROCESSED
    return collection_id


async def play(progression: Service, user: UUID, collection_id: UUID | None, rounds: int = 3):
    event = progression.game_finished(user, [100] * rounds, collection_id=collection_id)
    assert await progression.deliver(event) is Outcome.PROCESSED
    return event


# ------------------------------------------------------------------ свой сборник
async def test_owner_gets_nothing_for_own_collection(progression: Service) -> None:
    await progression.publish_config()
    owner = uuid7()
    collection = await publish_ugc(progression, owner)

    await play(progression, owner, collection)

    assert await progression.xp_total(owner) == 0
    assert await progression.balance(owner) == 0
    assert await progression.xp_rows(owner) == 0
    # Предмет учтён нулевой отметкой: повтор события не начислит задним числом.
    assert await progression.mark_rows(owner) == 2


async def test_other_player_is_rewarded_for_the_same_collection(progression: Service) -> None:
    await progression.publish_config()
    owner, player = uuid7(), uuid7()
    collection = await publish_ugc(progression, owner)

    await play(progression, player, collection)

    assert await progression.xp_total(player) == 3 * XP_PER_ROUND
    assert await progression.balance(player) == 3


async def test_owner_rewards_can_be_enabled_in_config(progression: Service) -> None:
    await progression.publish_config(ugc_guard={"own_collection_rewards": True})
    owner = uuid7()
    collection = await publish_ugc(progression, owner)

    await play(progression, owner, collection)

    assert await progression.xp_total(owner) == 3 * XP_PER_ROUND


# ------------------------------------------------------------------ повтор
async def test_repeat_within_window_gives_nothing_and_after_window_pays(
    progression: Service,
) -> None:
    await progression.publish_config()
    owner, player = uuid7(), uuid7()
    collection = await publish_ugc(progression, owner)

    await play(progression, player, collection)
    first = await progression.xp_total(player)
    progression.clock.advance(timedelta(hours=23))
    await play(progression, player, collection)
    assert await progression.xp_total(player) == first  # повтор в окне
    assert await progression.balance(player) == 3

    progression.clock.advance(timedelta(hours=2))  # окно 24 ч от последней награды прошло
    await play(progression, player, collection)
    assert await progression.xp_total(player) == 2 * first


async def test_replayed_event_after_window_does_not_pay_retroactively(
    progression: Service,
) -> None:
    await progression.publish_config()
    owner, player = uuid7(), uuid7()
    collection = await publish_ugc(progression, owner)
    await play(progression, player, collection)
    blocked = progression.game_finished(player, [100, 100, 100], collection_id=collection)
    assert await progression.deliver(blocked) is Outcome.PROCESSED
    assert await progression.xp_total(player) == 3 * XP_PER_ROUND

    progression.clock.advance(timedelta(days=3))
    again = progression.game_finished(
        player,
        [100, 100, 100],
        collection_id=collection,
        session_id=UUID(blocked.payload["session_id"]),
        finished_at=progression.clock.now() - timedelta(days=3),
    )
    await progression.deliver(again)

    assert await progression.xp_total(player) == 3 * XP_PER_ROUND


async def test_repeat_window_is_per_player_and_per_collection(progression: Service) -> None:
    await progression.publish_config()
    owner, first, second = uuid7(), uuid7(), uuid7()
    one, two = await publish_ugc(progression, owner), await publish_ugc(progression, owner)

    await play(progression, first, one)
    await play(progression, second, one)  # другой игрок
    await play(progression, first, two)  # другой сборник

    for user, expected in ((first, 2 * 3 * XP_PER_ROUND), (second, 3 * XP_PER_ROUND)):
        assert await progression.xp_total(user) == expected


async def test_repeat_window_zero_disables_the_check(progression: Service) -> None:
    await progression.publish_config(ugc_guard={"repeat_window_hours": 0})
    owner, player = uuid7(), uuid7()
    collection = await publish_ugc(progression, owner)

    await play(progression, player, collection)
    await play(progression, player, collection)

    assert await progression.xp_total(player) == 2 * 3 * XP_PER_ROUND


async def test_huge_window_means_never_again(progression: Service) -> None:
    await progression.publish_config(ugc_guard={"repeat_window_hours": 87_600})
    owner, player = uuid7(), uuid7()
    collection = await publish_ugc(progression, owner)

    await play(progression, player, collection)
    progression.clock.advance(timedelta(days=365))
    await play(progression, player, collection)

    assert await progression.xp_total(player) == 3 * XP_PER_ROUND


async def test_curated_collection_is_not_guarded_unless_scope_all(progression: Service) -> None:
    await progression.publish_config()
    player = uuid7()
    curated = await publish_ugc(progression, uuid7(), kind="curated")
    await play(progression, player, curated)
    await play(progression, player, curated)
    assert await progression.xp_total(player) == 2 * 3 * XP_PER_ROUND

    await progression.publish_config(version=2, ugc_guard={"repeat_scope": "all"})
    other = uuid7()
    await play(progression, other, curated)
    await play(progression, other, curated)
    assert await progression.xp_total(other) == 3 * XP_PER_ROUND


async def test_parallel_events_of_one_player_pay_once(progression: Service) -> None:
    await progression.publish_config()
    owner, player = uuid7(), uuid7()
    collection = await publish_ugc(progression, owner)
    events = [
        progression.game_finished(player, [100, 100, 100], collection_id=collection)
        for _ in range(4)
    ]

    await asyncio.gather(*(progression.deliver(e) for e in events))

    assert await progression.xp_total(player) == 3 * XP_PER_ROUND


# ------------------------------------------------------------------ два аккаунта и потолок
async def test_two_accounts_in_a_row_hit_the_daily_ugc_cap(progression: Service) -> None:
    """Владелец и его второй аккаунт гоняют разные сборники: суточный потолок UGC держит."""
    await progression.publish_config(ugc_guard={"ugc_daily_xp_cap": 100})
    owner, alt = uuid7(), uuid7()
    collections = [await publish_ugc(progression, owner) for _ in range(5)]

    for collection in collections:
        await play(progression, alt, collection)  # второй аккаунт проходит сборники первого
        await play(progression, owner, collection)  # сам владелец — ничего

    assert await progression.xp_total(owner) == 0
    assert await progression.xp_total(alt) == 100  # потолок, а не 5 * 90


async def test_ugc_cap_is_per_day(progression: Service) -> None:
    await progression.publish_config(ugc_guard={"ugc_daily_xp_cap": 100})
    owner, player = uuid7(), uuid7()
    first, second = await publish_ugc(progression, owner), await publish_ugc(progression, owner)

    await play(progression, player, first)
    progression.clock.advance(timedelta(days=1))
    await play(progression, player, second)

    assert await progression.xp_total(player) == 90 + 90


async def test_ugc_cap_does_not_limit_curated_or_plain_sessions(progression: Service) -> None:
    await progression.publish_config(ugc_guard={"ugc_daily_xp_cap": 10})
    player = uuid7()
    curated = await publish_ugc(progression, uuid7(), kind="curated")

    await play(progression, player, curated)
    await play(progression, player, None)  # без сборника

    assert await progression.xp_total(player) == 2 * 3 * XP_PER_ROUND


# ------------------------------------------------------------------ неизвестный владелец
async def test_unknown_collection_is_rewarded_as_before(progression: Service) -> None:
    """Старые данные без события CollectionPublished: награды не теряются."""
    await progression.publish_config()
    player = uuid7()

    await play(progression, player, uuid7())

    assert await progression.xp_total(player) == 3 * XP_PER_ROUND


async def test_backfilled_owner_is_guarded_from_then_on(progression: Service) -> None:
    await progression.publish_config()
    owner = uuid7()
    collection = uuid7()
    await play(progression, owner, collection)  # владелец ещё неизвестен
    assert await progression.xp_total(owner) == 3 * XP_PER_ROUND

    event = progression.collection_published(owner, collection_id=collection)
    await progression.deliver(event)
    progression.clock.advance(timedelta(days=2))
    await play(progression, owner, collection)

    assert await progression.xp_total(owner) == 3 * XP_PER_ROUND


async def test_collection_published_projection_is_idempotent(progression: Service) -> None:
    await progression.publish_config()
    owner = uuid7()
    collection = uuid7()
    event = progression.collection_published(owner, collection_id=collection)
    assert await progression.deliver(event) is Outcome.PROCESSED
    assert await progression.deliver(event) is Outcome.DUPLICATE
    hidden = progression.collection_published(owner, collection_id=collection, status="hidden")
    assert await progression.deliver(hidden) is Outcome.PROCESSED

    rows = await progression.rows("collection_authors")
    assert [(r.collection_id, r.author_id, r.kind) for r in rows] == [(collection, owner, "ugc")]


# ------------------------------------------------------------------ матчи
async def test_match_owner_gets_nothing_but_opponent_is_rewarded(progression: Service) -> None:
    await progression.publish_config()
    owner, friend = uuid7(), uuid7()
    collection = await publish_ugc(progression, owner)

    event = progression.duel(owner, friend, winner_rounds=2, loser_rounds=1)
    event = progression.match_finished(
        [
            {"user_id": owner, "place": 1, "outcome": "win"},
            {"user_id": friend, "place": 2, "outcome": "loss"},
        ],
        rounds=[[(owner, 10), (friend, 0)]] * 2 + [[(owner, 0), (friend, 10)]],
        collection_ids=[collection],
    )
    assert await progression.deliver(event) is Outcome.PROCESSED

    assert await progression.xp_total(owner) == 0
    assert await progression.balance(owner) == 0
    assert await progression.xp_total(friend) > 0
    assert await progression.balance(friend) > 0


async def test_match_repeat_in_window_gives_nothing(progression: Service) -> None:
    await progression.publish_config()
    owner, first, second = uuid7(), uuid7(), uuid7()
    collection = await publish_ugc(progression, owner)

    def lobby() -> Any:
        return progression.match_finished(
            [
                {"user_id": first, "place": 1, "outcome": "win"},
                {"user_id": second, "place": 2, "outcome": "loss"},
            ],
            rounds=[[(first, 10), (second, 0)]],
            collection_ids=[collection],
        )

    await progression.deliver(lobby())
    paid = await progression.xp_total(first)
    assert paid > 0
    rows = await progression.xp_rows(first)
    await progression.deliver(lobby())  # тот же сборник тем же игрокам в окне

    assert await progression.xp_total(first) == paid
    assert await progression.xp_rows(first) == rows
    assert await progression.xp_total(second) > 0


async def test_match_without_collection_is_untouched(progression: Service) -> None:
    await progression.publish_config()
    first, second = uuid7(), uuid7()

    await progression.deliver(progression.duel(first, second))
    once = await progression.xp_total(first)
    await progression.deliver(progression.duel(first, second))

    assert await progression.xp_total(first) > once  # без сборника повтор наград не режется


async def test_match_replay_after_own_refusal_does_not_pay_retroactively(
    progression: Service,
) -> None:
    """Отказ «свой сборник» нулевой отметкой: смена проекции задним числом не даёт награды."""
    await progression.publish_config()
    owner, friend = uuid7(), uuid7()
    collection = await publish_ugc(progression, owner)
    event = progression.match_finished(
        [
            {"user_id": owner, "place": 1, "outcome": "win"},
            {"user_id": friend, "place": 2, "outcome": "loss"},
        ],
        rounds=[[(owner, 10), (friend, 0)]],
        collection_ids=[collection],
    )
    await progression.deliver(event)
    assert await progression.xp_total(owner) == 0

    # Проекция изменилась (сборник стал кураторским), событие приходит повторно с новым id.
    await progression.deliver(
        progression.collection_published(None, collection_id=collection, kind="curated")
    )
    progression.clock.advance(timedelta(days=2))
    replay = progression.match_finished(
        [
            {"user_id": owner, "place": 1, "outcome": "win"},
            {"user_id": friend, "place": 2, "outcome": "loss"},
        ],
        rounds=[[(owner, 10), (friend, 0)]],
        collection_ids=[collection],
        match_id=UUID(event.payload["match_id"]),
        finished_at=progression.clock.now() - timedelta(days=2),
    )
    await progression.deliver(replay)

    assert await progression.xp_total(owner) == 0


async def test_ugc_cap_counts_boosted_match_xp_and_first_win_bonus(progression: Service) -> None:
    """Потолок UGC считается по итогу (бонус первой победы входит), а не по базе."""
    await progression.publish_config(ugc_guard={"ugc_daily_xp_cap": 100})
    owner, winner, loser = uuid7(), uuid7(), uuid7()
    collection = await publish_ugc(progression, owner)

    event = progression.match_finished(
        [
            {"user_id": winner, "place": 1, "outcome": "win"},
            {"user_id": loser, "place": 2, "outcome": "loss"},
        ],
        rounds=[[(winner, 10), (loser, 0)]] * 3,
        collection_ids=[collection],
    )
    await progression.deliver(event)

    assert await progression.xp_total(winner) <= 100
    assert await progression.xp_total(winner) > 0
