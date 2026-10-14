"""Инварианты агрегатов game: сессия, конфиги режимов, наборы дня, текст шэра."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from game_service.domain.daily import (
    DailySet,
    DailySetStatus,
    GeneratedBy,
    daily_seed,
    day_in_zone,
    next_reset_at,
)
from game_service.domain.errors import (
    DailyIndexOutOfRangeError,
    DailyLockedError,
    DraftConflictError,
    DraftNotFoundError,
    ItemDuplicateError,
    ItemNotPlayableError,
    ModeNotPublishedError,
    RoundNotActiveError,
    SessionNotActiveError,
    SessionNotFoundError,
)
from game_service.domain.events import DailyCompleted, GameFinished, ModeConfigPublished
from game_service.domain.modes import ModeLine, ModeStatus
from game_service.domain.session import GameSession, PlayerKind, PlayerRef, RoundPlan
from game_service.domain.share import CELL_EXACT, CELL_SKIP, CELL_VOID, share_text

from gv_core.ids import uuid7
from gv_game_kernel import (
    AssetRef,
    ConfigError,
    Guess,
    GuessKind,
    LevelLockedError,
    MatchVerdict,
    RoundFinishedError,
    RoundResult,
    RoundState,
)
from gv_messaging import EventEnvelope
from gv_testkit import FakeClock, assert_event_matches_schema

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
TODAY_DAY = NOW.date()
USER = PlayerRef(PlayerKind.USER, uuid7())
GUEST = PlayerRef(PlayerKind.GUEST, uuid7())


def session(
    player: PlayerRef = USER, *, assets: int = 3, voids: int = 0, day: date | None = TODAY_DAY
) -> GameSession:
    plan = [RoundPlan(uuid7(), AssetRef(uuid7(), 6)) for _ in range(assets)]
    plan += [RoundPlan(uuid7(), None) for _ in range(voids)]
    return GameSession.start(
        uuid7(),
        player=player,
        mode_key="music.daily.classic",
        mode_version=1,
        domain="music",
        source_type="daily",
        clue_type="audio_intro",
        day=day,
        preset_id=None,
        seed=b"seed",
        plan=plan,
        levels_count=6,
        max_points=6,
        idle_timeout_s=60,
        now=NOW,
    )


def exact(level: int = 1) -> RoundState:
    guess = Guess(GuessKind.ITEM, level, MatchVerdict.EXACT, "x")
    return RoundState(level=level, attempts_on_level=1, guesses=(guess,), outcome=RoundResult.EXACT)


def envelope(event: object) -> EventEnvelope:
    return EventEnvelope.from_domain_event(event, producer="game", clock=FakeClock(NOW))  # type: ignore[arg-type]


# ---------------------------------------------------------------- сессия
def test_rounds_without_assets_are_void_and_not_in_max_score() -> None:
    s = session(assets=2, voids=1)
    assert [r.is_void for r in s.rounds] == [False, False, True]
    assert s.max_score == 12
    assert s.current_index == 0


def test_moves_only_in_current_round_and_levels_locked() -> None:
    s = session()
    with pytest.raises(RoundNotActiveError):
        s.active_round(1)
    with pytest.raises(LevelLockedError):
        s.clue_round(0, 2)
    assert s.clue_round(0, 1).index == 0


def test_owner_check_hides_foreign_session() -> None:
    s = session()
    with pytest.raises(SessionNotFoundError):
        s.require_owner(GUEST)
    s.require_owner(USER)


def test_finishing_user_daily_records_both_events_valid_by_schema() -> None:
    s = session(assets=2, voids=1)
    s.mark_clue_issued(0, NOW)
    s.apply(0, exact(2), points=5, now=NOW + timedelta(seconds=3))
    s.apply(1, replace(RoundState(), outcome=RoundResult.GAVE_UP), points=0, now=NOW)
    assert s.status.value == "finished"
    assert s.version == 3
    events = s.pull_events()
    assert [type(e) for e in events] == [GameFinished, DailyCompleted]
    finished = events[0]
    assert isinstance(finished, GameFinished)
    assert finished.score == 5 and finished.max_score == 12
    assert finished.rounds[0].elapsed_ms == 3000 and finished.rounds[0].level == 2
    assert finished.rounds[2].outcome == "void"
    for e in events:
        assert_event_matches_schema(envelope(e))
    with pytest.raises(SessionNotActiveError):
        s.active_round(0)


def test_guest_session_has_no_daily_completed() -> None:
    s = session(GUEST, assets=1)
    s.apply(0, exact(), points=6, now=NOW)
    (event,) = s.pull_events()
    assert isinstance(event, GameFinished) and event.is_guest and event.user_id is None
    assert not s.counts_in_daily_stats


def test_void_round_after_finish_is_noop_and_finished_round_rejects_moves() -> None:
    s = session(assets=2)
    s.apply(0, exact(), points=6, now=NOW)
    assert s.void_round(0, NOW) is None
    with pytest.raises(RoundFinishedError):
        s.active_round(0)
    r = s.void_round(1, NOW)
    assert r is not None and r.is_void and s.max_score == 6


def test_abandon_only_active() -> None:
    s = session()
    assert s.abandon(NOW) is True
    assert s.abandon(NOW) is False


def test_all_void_session_finishes_at_start() -> None:
    s = session(assets=0, voids=2)
    assert s.status.value == "finished" and s.max_score == 0


# ---------------------------------------------------------------- конфиги режимов
BODY = {"key": "music.daily.classic"}


def test_draft_revision_and_publish_flow() -> None:
    line = ModeLine("music.daily.classic")
    assert line.status is ModeStatus.DRAFT
    line.save_draft(
        BODY,
        base_revision=None,
        validation={"ok": False, "errors": [{"path": "x", "code": "y"}]},
        actor_id=None,
        now=NOW,
    )
    with pytest.raises(DraftConflictError):
        line.save_draft(BODY, base_revision=None, validation={"ok": True}, actor_id=None, now=NOW)
    with pytest.raises(ConfigError):
        line.publish_draft(1, actor_id=None, reason="r", now=NOW)
    line.save_draft(BODY, base_revision=1, validation={"ok": True}, actor_id=None, now=NOW)
    with pytest.raises(DraftConflictError):
        line.publish_draft(1, actor_id=None, reason="r", now=NOW)
    version = line.publish_draft(2, actor_id=None, reason="r", now=NOW)
    assert version.version == 1 and line.draft is None
    assert str(line.status) == ModeStatus.PUBLISHED.value
    (event,) = line.pull_events()
    assert isinstance(event, ModeConfigPublished)
    assert_event_matches_schema(envelope(event))
    with pytest.raises(DraftNotFoundError):
        line.require_draft(1)


def test_rollback_and_archive() -> None:
    line = ModeLine("music.daily.classic")
    v1 = line.publish(BODY, actor_id=None, reason="r", now=NOW)
    v2 = line.publish({**BODY, "x": 1}, actor_id=None, reason="r", now=NOW)
    v3 = line.rollback(v1, actor_id=None, reason="r", now=NOW)
    assert (v2.version, v3.version, v3.rolled_back_from, v3.body_hash) == (2, 3, 1, v1.body_hash)
    line.archive(actor_id=None, now=NOW)
    assert line.status is ModeStatus.ARCHIVED
    with pytest.raises(ModeNotPublishedError):
        line.archive(actor_id=None, now=NOW)
    events = line.pull_events()
    for e in events:
        assert_event_matches_schema(envelope(e))


# ---------------------------------------------------------------- наборы дня
TODAY = date(2026, 10, 10)


def daily(day: date = date(2026, 10, 11)) -> DailySet:
    return DailySet.generated(
        "music.daily.classic",
        day,
        mode_version=1,
        items=[uuid7() for _ in range(3)],
        generated_by=GeneratedBy.SCHEDULE,
    )


def test_replace_rules() -> None:
    d = daily()
    with pytest.raises(ItemNotPlayableError):
        d.replace_item(0, uuid7(), playable=False, today=TODAY)
    with pytest.raises(ItemDuplicateError):
        d.replace_item(0, d.items[1], playable=True, today=TODAY)
    with pytest.raises(DailyIndexOutOfRangeError):
        d.replace_item(5, uuid7(), playable=True, today=TODAY)
    new = uuid7()
    d.replace_item(0, new, playable=True, today=TODAY)
    assert d.items[0] == new and d.replaced == {0}
    with pytest.raises(DailyLockedError):
        daily(TODAY).replace_item(0, uuid7(), playable=True, today=TODAY)


def test_lock_and_failed_set() -> None:
    d = daily()
    d.lock(NOW)
    assert d.status is DailySetStatus.LOCKED and d.locked_at == NOW
    with pytest.raises(DailyLockedError):
        d.reroll([uuid7()], mode_version=1, generated_by=GeneratedBy.ADMIN)
    failed = DailySet.generated(
        "k.a.b", TODAY, mode_version=1, items=[], generated_by=GeneratedBy.LAZY
    )
    assert failed.status is DailySetStatus.FAILED and not failed.is_playable


def test_regenerate_failed_set_allowed_on_started_day() -> None:
    """Проваленный набор перегенерируется и в начавшийся день (его день = сегодня).

    Регресс: засев e2e падал — набор дня «сегодня» создавался ``failed`` до готовности
    ассетов, а админская перегенерация отвечала 409 (``is_locked_on`` истинна, т.к.
    ``day <= today``), и набор навсегда оставался пустым.
    """
    failed = DailySet.generated(
        "music.daily.classic", TODAY, mode_version=1, items=[], generated_by=GeneratedBy.SCHEDULE
    )
    assert failed.is_locked_on(TODAY)  # день набора уже наступил
    items = [uuid7() for _ in range(3)]
    failed.regenerate(items, mode_version=2, generated_by=GeneratedBy.ADMIN, today=TODAY)
    assert failed.status is DailySetStatus.SCHEDULED and failed.items == items


def test_regenerate_scheduled_set_frozen_on_started_day() -> None:
    """Запланированный набор в начавшийся день остаётся «только для чтения» (инвариант дня)."""
    scheduled = daily(TODAY)  # scheduled, день = сегодня
    assert scheduled.status is DailySetStatus.SCHEDULED
    with pytest.raises(DailyLockedError):
        scheduled.regenerate(
            [uuid7()], mode_version=2, generated_by=GeneratedBy.ADMIN, today=TODAY
        )


def test_seed_and_day_boundaries() -> None:
    secret = b"s"
    assert daily_seed(secret, "k", TODAY) == daily_seed(secret, "k", TODAY)
    assert daily_seed(secret, "k", TODAY) != daily_seed(secret, "k", TODAY, 2)
    assert daily_seed(secret, "k", TODAY) != daily_seed(b"other", "k", TODAY)
    late = datetime(2026, 10, 10, 21, 30, tzinfo=UTC)  # 00:30 по Москве
    assert day_in_zone(late, "Europe/Moscow") == date(2026, 10, 11)
    assert next_reset_at(NOW, "Europe/Moscow") == datetime(2026, 10, 10, 21, 0, tzinfo=UTC)


# ---------------------------------------------------------------- шэр
def test_share_text_has_no_answers() -> None:
    skip = Guess(GuessKind.SKIP, 1, None, "")
    hit = Guess(GuessKind.ITEM, 2, MatchVerdict.EXACT, "Секретное название")
    rounds = [
        RoundState(level=2, guesses=(skip, hit), outcome=RoundResult.EXACT),
        RoundState(outcome=RoundResult.VOID),
    ]
    text = share_text(
        title="OnCue",
        mode_key="music.daily.classic",
        day=TODAY,
        score=5,
        max_score=6,
        rounds=rounds,
        url="https://x/",
    )
    assert text.splitlines() == [
        "OnCue music.daily.classic 2026-10-10",
        "5/6",
        CELL_SKIP + CELL_EXACT,
        CELL_VOID,
        "https://x/",
    ]
    assert "Секретное" not in text
