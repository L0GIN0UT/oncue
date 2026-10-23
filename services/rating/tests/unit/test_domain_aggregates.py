"""Агрегаты rating: рейтинг игрока, сезон, линия версий ladder-а, неделя топа."""

from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from rating_service.domain.elo import EloCalculator, Outcome
from rating_service.domain.errors import (
    LadderInvalidError,
    SeasonNotActiveError,
    VersionNotDraftError,
    VersionNotFoundError,
    VersionNotPublishedError,
)
from rating_service.domain.events import (
    LadderPublished,
    RatingChanged,
    SeasonFinished,
    SeasonStarted,
)
from rating_service.domain.ladder import LadderConfig, SoftReset
from rating_service.domain.ladder_line import LadderLine, VersionStatus
from rating_service.domain.rating import Rating, soft_reset_value
from rating_service.domain.season import ClosedBy, Season, SeasonStatus
from rating_service.domain.weekly import week_start

from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

EXAMPLE = Path(__file__).resolve().parents[4] / "contracts" / "rating" / "examples"
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
ACTOR = uuid7()


def raw_config() -> dict[str, Any]:
    data: dict[str, Any] = copy.deepcopy(
        json.loads((EXAMPLE / "ladder.film.v1.json").read_text(encoding="utf-8"))
    )
    return data


def config() -> LadderConfig:
    return LadderConfig.parse(raw_config())


def play(rating: Rating, cfg: LadderConfig, outcome: Outcome, opponent: Rating) -> RatingChanged:
    """Матч двух игроков; возвращает событие первого."""
    calc = EloCalculator(cfg.elo, cfg.calibration)
    a, b = calc.settle(
        rating.side(outcome, left=False), opponent.side(_flip(outcome), left=False), "classic"
    )
    match_id = uuid7()
    event = rating.apply_match(
        cfg, a, match_id=match_id, queue_format="classic", outcome=outcome, at=NOW
    )
    opponent.apply_match(
        cfg, b, match_id=match_id, queue_format="classic", outcome=_flip(outcome), at=NOW
    )
    return event


def _flip(outcome: Outcome) -> Outcome:
    return {Outcome.WIN: Outcome.LOSS, Outcome.LOSS: Outcome.WIN, Outcome.DRAW: Outcome.DRAW}[
        outcome
    ]


# ------------------------------------------------------------------ Rating
def test_new_player_starts_from_config() -> None:
    cfg = config()
    rating = Rating.start(uuid7(), uuid7(), cfg, NOW)
    assert (rating.rating, rating.matches_total, rating.season_matches, rating.event_version) == (
        cfg.elo.start,
        0,
        0,
        0,
    )


def test_event_versions_are_monotonic_and_tier_hidden_in_calibration() -> None:
    cfg = config()
    a, b = Rating.start(uuid7(), uuid7(), cfg, NOW), Rating.start(uuid7(), uuid7(), cfg, NOW)
    events = [play(a, cfg, Outcome.WIN, b) for _ in range(cfg.calibration.matches)]
    assert [e.version for e in events] == list(range(1, cfg.calibration.matches + 1))
    assert all(e.tier is None and e.old_tier is None for e in events[:-1])
    assert [e.calibration_left for e in events] == list(range(cfg.calibration.matches - 1, -1, -1))
    last = events[-1]
    assert last.calibrating is False
    assert last.tier == cfg.tier_for(last.new).key
    assert last.old_tier is None
    assert last.matches_played == cfg.calibration.matches
    assert a.pending_events == tuple(events)
    assert last.new - last.old == last.delta


def test_tier_is_visible_in_calibration_when_config_does_not_hide_it() -> None:
    raw = raw_config()
    raw["calibration"]["hide_tier"] = False
    cfg = LadderConfig.parse(raw)
    a, b = Rating.start(uuid7(), uuid7(), cfg, NOW), Rating.start(uuid7(), uuid7(), cfg, NOW)
    event = play(a, cfg, Outcome.WIN, b)
    assert event.calibrating is True
    assert event.tier == cfg.tier_for(event.new).key


def test_leaver_penalty_is_reported_in_event() -> None:
    cfg = config()
    a, b = Rating.start(uuid7(), uuid7(), cfg, NOW), Rating.start(uuid7(), uuid7(), cfg, NOW)
    calc = EloCalculator(cfg.elo, cfg.calibration)
    _, leaver_delta = calc.settle(
        a.side(Outcome.WIN, left=False), b.side(Outcome.LOSS, left=True), "classic"
    )
    event = b.apply_match(
        cfg, leaver_delta, match_id=uuid7(), queue_format="classic", outcome=Outcome.LOSS, at=NOW
    )
    assert event.leaver_penalty == cfg.elo.leaver_penalty
    assert event.delta == event.new - event.old < 0


def test_carry_over_applies_soft_reset_and_keeps_calibration_and_version() -> None:
    cfg = config()
    a, b = Rating.start(uuid7(), uuid7(), cfg, NOW), Rating.start(uuid7(), uuid7(), cfg, NOW)
    for _ in range(3):
        play(a, cfg, Outcome.WIN, b)
    reset = cfg.season.soft_reset
    new_season = uuid7()
    carried = a.carry_over(new_season, reset, NOW)
    assert carried.season_id == new_season
    assert carried.rating == soft_reset_value(a.rating, reset)
    assert (carried.matches_total, carried.season_matches) == (3, 0)
    assert carried.event_version == a.event_version


@pytest.mark.parametrize(
    ("old", "expected"), [(1000, 1000), (1400, 1200), (600, 800), (1001, 1001), (1003, 1002)]
)
def test_soft_reset_formula(old: int, expected: int) -> None:
    assert soft_reset_value(old, SoftReset(target=1000, ratio=0.5)) == expected


@given(
    old=st.integers(-3000, 5000),
    delta=st.integers(0, 3000),
    ratio=st.floats(0, 1, allow_nan=False),
)
def test_soft_reset_is_monotonic_and_pulls_toward_target(
    old: int, delta: int, ratio: float
) -> None:
    reset = SoftReset(target=1000, ratio=ratio)
    assert soft_reset_value(old, reset) <= soft_reset_value(old + delta, reset)
    assert abs(soft_reset_value(old, reset) - 1000) <= abs(old - 1000) + 1


# ------------------------------------------------------------------ Season
def make_season(start: datetime = NOW) -> Season:
    return Season.plan(uuid7(), config(), ladder_version=1, number=1, starts_at=start)


def test_season_length_comes_from_config() -> None:
    season = make_season()
    assert season.ends_at - season.starts_at == timedelta(days=config().season.length_days)
    assert season.status is SeasonStatus.SCHEDULED


def test_first_season_start_has_no_reset_and_next_carries_formula() -> None:
    cfg = config()
    first = make_season()
    first.activate(cfg, reset_applied=False)
    second = make_season()
    second.activate(cfg, reset_applied=True)
    started_first, started_second = first.pending_events[0], second.pending_events[0]
    assert isinstance(started_first, SeasonStarted)
    assert isinstance(started_second, SeasonStarted)
    assert started_first.soft_reset is None
    assert started_second.soft_reset == {
        "target": cfg.season.soft_reset.target,
        "ratio": cfg.season.soft_reset.ratio,
    }


def test_finish_requires_active_and_records_event() -> None:
    cfg = config()
    season = make_season()
    with pytest.raises(SeasonNotActiveError):
        season.finish(NOW, ClosedBy.ADMIN, 0)
    season.activate(cfg, reset_applied=False)
    season.pull_events()
    season.finish(NOW + timedelta(days=1), ClosedBy.SCHEDULE, 5)
    (event,) = season.pending_events
    assert isinstance(event, SeasonFinished)
    assert (event.players_count, event.closed_by) == (5, "schedule")
    assert event.standings_ref == f"/v1/ladders/film/leaderboard?season={season.season_id}"
    with pytest.raises(SeasonNotActiveError):
        season.finish(NOW, ClosedBy.ADMIN, 0)


def test_due_and_overlap() -> None:
    season = make_season()
    season.activate(config(), reset_applied=False)
    assert not season.is_due(season.ends_at - timedelta(seconds=1))
    assert season.is_due(season.ends_at)
    assert season.overlaps(season.ends_at - timedelta(days=1), season.ends_at + timedelta(days=1))
    assert not season.overlaps(season.ends_at, season.ends_at + timedelta(days=1))


# ------------------------------------------------------------------ LadderLine
def make_line() -> LadderLine:
    return LadderLine.create(config(), actor_id=ACTOR, reason="сид", now=NOW)


def test_create_publishes_version_one() -> None:
    line = make_line()
    assert line.published is not None
    assert line.published.version == 1
    (event,) = line.pending_events
    assert isinstance(event, LadderPublished)
    assert event.matchmaking["window"]["base"] == config().matchmaking.window.base
    assert event.formats == ("classic", "lite")


def test_draft_publish_archives_previous() -> None:
    line = make_line()
    line.pull_events()
    raw = raw_config()
    raw["elo"]["k"] = 30
    draft = line.save_draft(LadderConfig.parse(raw), actor_id=ACTOR, reason="баланс", now=NOW)
    assert draft.version == 2
    again = line.save_draft(LadderConfig.parse(raw), actor_id=ACTOR, reason="ещё раз", now=NOW)
    assert again is draft
    line.publish(2, now=NOW)
    assert [(v.version, v.status) for v in line.versions] == [
        (1, VersionStatus.ARCHIVED),
        (2, VersionStatus.PUBLISHED),
    ]
    (event,) = line.pending_events
    assert isinstance(event, LadderPublished)
    assert event.version == 2


def test_publish_rejects_non_draft_and_unknown() -> None:
    line = make_line()
    with pytest.raises(VersionNotDraftError):
        line.publish(1, now=NOW)
    with pytest.raises(VersionNotFoundError):
        line.publish(9, now=NOW)


def test_rollback_copies_old_version_as_new_one() -> None:
    line = make_line()
    raw = raw_config()
    raw["elo"]["k"] = 30
    line.save_draft(LadderConfig.parse(raw), actor_id=ACTOR, reason="баланс", now=NOW)
    line.publish(2, now=NOW)
    line.pull_events()
    copy_ = line.rollback(1, actor_id=ACTOR, reason="откат", now=NOW)
    assert copy_.version == 3
    assert copy_.config == line.get(1).config
    assert line.published is copy_
    assert line.get(2).status is VersionStatus.ARCHIVED
    assert len(line.pending_events) == 1


def test_rollback_to_never_published_version_is_rejected() -> None:
    line = make_line()
    draft = line.save_draft(config(), actor_id=ACTOR, reason="черновик", now=NOW)
    with pytest.raises(VersionNotPublishedError):
        line.rollback(draft.version, actor_id=ACTOR, reason="откат", now=NOW)


def test_draft_cannot_change_key_or_domain() -> None:
    line = make_line()
    raw = raw_config()
    raw["key"] = "other"
    raw["domain"] = "music"
    with pytest.raises(LadderInvalidError) as caught:
        line.save_draft(LadderConfig.parse(raw), actor_id=ACTOR, reason="плохо", now=NOW)
    assert {e["path"] for e in caught.value.details["errors"]} == {"/key", "/domain"}


# ------------------------------------------------------------------ неделя
@pytest.mark.parametrize(
    ("at", "zone", "expected"),
    [
        (datetime(2026, 10, 19, 0, 30, tzinfo=UTC), "Europe/Moscow", date(2026, 10, 19)),
        (datetime(2026, 10, 18, 22, 0, tzinfo=UTC), "Europe/Moscow", date(2026, 10, 19)),
        (datetime(2026, 10, 18, 20, 59, tzinfo=UTC), "Europe/Moscow", date(2026, 10, 12)),
        (datetime(2026, 10, 25, 23, 59, tzinfo=UTC), "UTC", date(2026, 10, 19)),
        (datetime(2026, 10, 26, 0, 0, tzinfo=UTC), "UTC", date(2026, 10, 26)),
    ],
)
def test_week_starts_on_monday_in_configured_zone(at: datetime, zone: str, expected: date) -> None:
    assert week_start(at, zone) == expected
    assert week_start(at, zone).weekday() == 0


def test_week_start_requires_aware_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        week_start(datetime(2026, 10, 19, tzinfo=None), "UTC")  # noqa: DTZ001 — проверка отказа
