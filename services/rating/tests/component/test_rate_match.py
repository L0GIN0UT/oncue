"""Consumer ``match.MatchFinished``: рейтинговая дуэль → рейтинги, история, события (G3)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from gv_core.ids import uuid7
from gv_messaging import Outcome
from gv_testkit import assert_event_matches_schema, load_example

pytestmark = pytest.mark.component

RatingService = Any  # фикстура `rating` описана в conftest.py


def changed_for(events: list[Any], user_id: UUID) -> list[dict[str, Any]]:
    return [e.payload for e in events if e.payload["user_id"] == str(user_id)]


async def test_first_match_changes_both_ratings_and_publishes_events(rating: RatingService) -> None:
    season_id = await rating.seed()
    winner, loser = uuid7(), uuid7()
    match = await rating.play(winner, loser)

    events = await rating.outbox("rating.RatingChanged")
    assert len(events) == 2
    for envelope in events:
        assert_event_matches_schema(envelope)
        assert envelope.topic == "rating.rating.v1"
    won = changed_for(events, winner)[0]
    lost = changed_for(events, loser)[0]
    assert (won["old"], won["new"], won["delta"]) == (1000, 1020, 20)
    assert (lost["old"], lost["new"], lost["delta"]) == (1000, 980, -20)
    assert won["version"] == lost["version"] == 1
    assert won["match_id"] == str(match.payload["match_id"])
    assert won["season_id"] == str(season_id)
    assert won["calibrating"] is True
    assert won["calibration_left"] == 9
    assert won["tier"] is None
    assert won["outcome"] == "win"
    assert won["format"] == "classic"
    assert won["leaver_penalty"] == 0


async def test_duplicate_match_in_new_event_is_noop(rating: RatingService) -> None:
    await rating.seed()
    first = await rating.play(uuid7(), uuid7())
    again = first.model_copy(update={"event_id": uuid7()})
    assert await rating.deliver(again) is Outcome.PROCESSED
    assert len(await rating.outbox("rating.RatingChanged")) == 2


async def test_redelivered_event_is_deduplicated_by_inbox(rating: RatingService) -> None:
    await rating.seed()
    event = rating.match_event(uuid7(), uuid7())
    assert await rating.deliver(event) is Outcome.PROCESSED
    assert await rating.deliver(event) is Outcome.DUPLICATE
    assert len(await rating.outbox("rating.RatingChanged")) == 2


@pytest.mark.parametrize(
    "override",
    [
        {"ranked": False, "ladder": None, "queue_format": None},
        {"aborted": True},
        {"format": "party"},
    ],
    ids=["not_ranked", "aborted", "party"],
)
async def test_unrated_matches_change_nothing(
    rating: RatingService, override: dict[str, Any]
) -> None:
    await rating.seed()
    winner, loser = uuid7(), uuid7()
    event = rating.match_event(winner, loser, **override)
    assert await rating.deliver(event) is Outcome.PROCESSED
    assert await rating.outbox("rating.RatingChanged") == []
    state = await rating.rating_of(winner)
    assert state["matches_played"] == 0


async def test_versions_grow_per_user_and_ladder(rating: RatingService) -> None:
    await rating.seed()
    a, b, c = uuid7(), uuid7(), uuid7()
    await rating.play(a, b)
    await rating.play(b, c)
    await rating.play(a, c)
    events = await rating.outbox("rating.RatingChanged")
    assert [p["version"] for p in changed_for(events, a)] == [1, 2]
    assert [p["version"] for p in changed_for(events, b)] == [1, 2]
    assert [p["version"] for p in changed_for(events, c)] == [1, 2]
    assert [p["matches_played"] for p in changed_for(events, a)] == [1, 2]


async def test_ladders_are_independent(rating: RatingService) -> None:
    await rating.seed("film")
    await rating.seed("music")
    a, b = uuid7(), uuid7()
    await rating.play(a, b, ladder="film")
    assert (await rating.rating_of(a, "film"))["matches_played"] == 1
    assert (await rating.rating_of(a, "music"))["matches_played"] == 0


async def test_leaver_gets_penalty_and_winner_plain_win(rating: RatingService) -> None:
    await rating.seed()
    winner, leaver = uuid7(), uuid7()
    await rating.play(winner, leaver, loser_finish="left")
    events = await rating.outbox("rating.RatingChanged")
    lost = changed_for(events, leaver)[0]
    won = changed_for(events, winner)[0]
    assert lost["leaver_penalty"] == 10
    assert lost["delta"] == -20 - 10
    assert won["leaver_penalty"] == 0
    assert won["delta"] == 20


async def test_lite_queue_uses_format_multiplier(rating: RatingService) -> None:
    await rating.seed()
    winner, loser = uuid7(), uuid7()
    await rating.play(winner, loser, queue_format="lite")
    won = changed_for(await rating.outbox("rating.RatingChanged"), winner)[0]
    assert won["format"] == "lite"
    assert won["delta"] == 16  # K=40 в калибровке × 0,5 × множитель lite 0,8


async def test_draw_between_equals_changes_nothing(rating: RatingService) -> None:
    await rating.seed()
    a, b = uuid7(), uuid7()
    await rating.play(a, b, draw=True)
    assert {
        p["delta"] for p in [e.payload for e in await rating.outbox("rating.RatingChanged")]
    } == {0}


async def test_calibration_ends_after_configured_matches(rating: RatingService) -> None:
    await rating.seed(calibration__matches=3)
    a, b = uuid7(), uuid7()
    for _ in range(2):
        await rating.play(a, b)
    during = await rating.rating_of(a)
    assert during["calibrating"] is True
    assert during["rating"] is None
    assert during["tier"] is None
    assert during["calibration_left"] == 1
    await rating.play(a, b)
    done = await rating.rating_of(a)
    assert done["calibrating"] is False
    assert done["rating"] is not None
    assert done["tier"] is not None
    assert done["calibration_left"] == 0
    last = changed_for(await rating.outbox("rating.RatingChanged"), a)[-1]
    assert last["calibrating"] is False
    assert last["old_tier"] is None
    assert last["tier"] == done["tier"]["key"]


async def test_contract_violations_go_to_dead_letters(rating: RatingService) -> None:
    await rating.seed()
    one_player = rating.match_event(uuid7(), uuid7())
    payload = {**one_player.payload, "players": one_player.payload["players"][:1]}
    assert await rating.deliver(one_player.model_copy(update={"payload": payload})) is Outcome.DEAD

    both_win = rating.match_event(uuid7(), uuid7())
    players = [{**p, "outcome": "win"} for p in both_win.payload["players"]]
    assert (
        await rating.deliver(
            both_win.model_copy(update={"payload": {**both_win.payload, "players": players}})
        )
        is Outcome.DEAD
    )
    unknown = rating.match_event(uuid7(), uuid7(), ladder="nope")
    assert await rating.deliver(unknown) is Outcome.DEAD
    no_format = rating.match_event(uuid7(), uuid7(), queue_format=None)
    assert await rating.deliver(no_format) is Outcome.DEAD
    assert await rating.outbox("rating.RatingChanged") == []


async def test_no_active_season_is_retried_not_lost(rating: RatingService) -> None:
    await rating.seed()
    async with rating.container.uow_factory()() as uow:
        season = await uow.seasons.active("film")
        assert season is not None
        season.status = type(season.status)("finished")
        await uow.seasons.save(season)
        await uow.commit()
    event = rating.match_event(uuid7(), uuid7())
    assert await rating.deliver(event) is Outcome.DEAD  # ретраи исчерпаны → DLQ
    assert await rating.outbox("rating.RatingChanged") == []
    # Ничего не отмечено обработанным: после появления сезона тот же матч считается.
    async with rating.container.uow_factory()() as uow:
        from rating_service.application.ports import PublishedLadder

        published = await uow.ladders.published("film")
        assert isinstance(published, PublishedLadder)
        await rating.container.lifecycle.start(uow, published)
        await uow.commit()
    replay = event.model_copy(update={"event_id": uuid7()})
    assert await rating.deliver(replay) is Outcome.PROCESSED
    assert len(await rating.outbox("rating.RatingChanged")) == 2


async def test_example_match_finished_is_processed(rating: RatingService) -> None:
    """Пример контракта (не рейтинговый) принимается и ничего не меняет."""
    await rating.seed()
    assert await rating.deliver(load_example("match.MatchFinished")) is Outcome.PROCESSED
    assert await rating.outbox("rating.RatingChanged") == []
