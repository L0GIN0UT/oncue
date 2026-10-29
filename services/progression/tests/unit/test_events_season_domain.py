"""Домен сезонных событий: определение, статус по времени, триггеры, прогресс (ADR 0054-0056)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from progression_service.domain import metrics
from progression_service.domain import wheel as wheel_domain
from progression_service.domain.events_season import (
    EVENT_PERIOD_KEY,
    FindState,
    RunStatus,
    canonical_sha256,
    complete_op_id,
    definition_errors,
    effective_end,
    find_op_id,
    find_state,
    others_done,
    parse_definition,
    run_status,
)
from progression_service.domain.metrics import MetricUpdate

pytestmark = pytest.mark.unit

COLL_A = "0190a000-0000-7000-8000-00000000000a"
COLL_B = "0190a000-0000-7000-8000-00000000000b"

T0 = datetime(2026, 11, 1, tzinfo=UTC)
T1 = T0 + timedelta(days=7)


def find(key: str, number: int, trigger: dict[str, Any], *, final: bool = False) -> dict[str, Any]:
    return {
        "key": key,
        "number": number,
        "title": key,
        "diary_before": "до",
        "diary_after": "после",
        "x_pct": 1,
        "y_pct": 2,
        "reward": {"xp": 10, "verses": 5},
        "final": final,
        "trigger": trigger,
    }


def server(metric: str, target: int = 3, period: str = "event", **params: Any) -> dict[str, Any]:
    trigger: dict[str, Any] = {
        "type": "server",
        "metric": metric,
        "target": target,
        "period": period,
    }
    if params:
        trigger["params"] = params
    return trigger


def body(*finds: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": "Осень",
        "description": "",
        "theme_key": "autumn",
        "finds": list(finds),
        "reward": {"item_key": "frame_leaf"},
    }


SPOT = {"type": "ui_spot", "page_key": "home"}


def sample() -> dict[str, Any]:
    return body(
        find("b", 2, server("rounds_played")),
        find("a", 1, SPOT),
        find("z", 3, SPOT, final=True),
    )


def test_parse_sorts_finds_by_number_and_reads_reward() -> None:
    definition = parse_definition(sample())
    assert [f.key for f in definition.finds] == ["a", "b", "z"]
    assert definition.item_key == "frame_leaf"
    assert definition.total == 3
    assert definition.find("b") is not None
    assert definition.find("nope") is None
    assert [f.key for f in definition.server_finds()] == ["b"]


def test_parse_reads_optional_task_i18n() -> None:
    raw = sample()
    raw["finds"][1]["task_i18n"] = "event.sample.a.task"
    finds = {f.key: f for f in parse_definition(raw).finds}
    assert finds["a"].task_i18n == "event.sample.a.task"
    assert finds["b"].task_i18n is None


def test_valid_definition_has_no_errors() -> None:
    assert definition_errors(parse_definition(sample()), slot_keys={"frame"}) == []


def test_duplicate_keys_and_numbers_are_reported() -> None:
    raw = body(find("a", 1, SPOT), find("a", 1, SPOT), find("z", 2, SPOT, final=True))
    reasons = " ".join(e["path"] for e in definition_errors(parse_definition(raw), slot_keys=None))
    assert "finds[].key" in reasons
    assert "finds[].number" in reasons


def test_exactly_one_final_is_required() -> None:
    raw = body(find("a", 1, SPOT), find("b", 2, SPOT))
    errors = definition_errors(parse_definition(raw), slot_keys=None)
    assert [e["path"] for e in errors] == ["finds"]


@pytest.mark.parametrize(
    ("trigger", "path_part"),
    [
        (server("weekly_top_place", 1), "params"),
        (server("item_equipped", 1), "params"),
        (server("item_equipped", 1, slot="frame", item_key="x"), "params"),
        (server("item_equipped", 2, slot="frame"), "target"),
        (server("item_equipped", 1, slot="unknown"), "slot"),
        (server("rounds_played", 1, place_max=3), "place_max"),
        (server("rounds_played", 1, slot="frame"), "params"),
        (server("rounds_played", 1, collection_keys=[COLL_A]), "collection_keys"),
        (server("collection_played", 1, collection_keys=[COLL_A, COLL_A]), "collection_keys"),
    ],
)
def test_trigger_parameter_rules(trigger: dict[str, Any], path_part: str) -> None:
    raw = body(find("a", 1, trigger), find("z", 2, SPOT, final=True))
    errors = definition_errors(parse_definition(raw), slot_keys={"frame"})
    assert errors
    assert any(path_part in e["path"] for e in errors)


def test_status_follows_time_for_published_run() -> None:
    assert run_status("published", T0, T1, T0 - timedelta(seconds=1)) is RunStatus.PUBLISHED
    assert run_status("published", T0, T1, T0) is RunStatus.ACTIVE
    assert run_status("active", T0, T1, T1 - timedelta(seconds=1)) is RunStatus.ACTIVE
    assert run_status("active", T0, T1, T1) is RunStatus.FINISHED
    # tick ещё не отработал: хранимый published, а время вышло - читаем finished
    assert run_status("published", T0, T1, T1 + timedelta(days=1)) is RunStatus.FINISHED


def test_draft_and_stopped_do_not_depend_on_time() -> None:
    assert run_status("draft", T0, T1, T0 + timedelta(days=1)) is RunStatus.DRAFT
    assert run_status("stopped", T0, T1, T0 + timedelta(days=1)) is RunStatus.STOPPED


def test_effective_end_is_earlier_of_end_and_stop() -> None:
    assert effective_end(T1, None) == T1
    assert effective_end(T1, T0 + timedelta(days=1)) == T0 + timedelta(days=1)
    assert effective_end(T1, T1 + timedelta(days=1)) == T1


def test_hash_ignores_key_order_and_detects_change() -> None:
    a = {"x": 1, "y": [1, 2]}
    assert canonical_sha256(a) == canonical_sha256({"y": [1, 2], "x": 1})
    assert canonical_sha256(a) != canonical_sha256({"x": 2, "y": [1, 2]})


def test_op_ids() -> None:
    run, user = UUID(int=1), UUID(int=2)
    assert find_op_id(run, "a", user) == f"event:{run}:a:{user}"
    assert complete_op_id(run, user) == f"event:{run}:complete:{user}"


def trigger_of(metric: str, **params: Any) -> Any:
    raw = body(find("a", 1, server(metric, 3, "day", **params)), find("z", 2, SPOT, final=True))
    return parse_definition(raw).finds[0].trigger


def test_trigger_accepts_only_its_metric_and_domain() -> None:
    plain = trigger_of("rounds_played")
    assert plain.accepts(MetricUpdate(metrics.ROUNDS_PLAYED, 1, "film"))
    assert not plain.accepts(MetricUpdate(metrics.WINS, 1))
    music = trigger_of("rounds_played", domain="music")
    assert music.accepts(MetricUpdate(metrics.ROUNDS_PLAYED, 1, "music"))
    assert not music.accepts(MetricUpdate(metrics.ROUNDS_PLAYED, 1, "film"))


def test_weekly_top_place_threshold() -> None:
    t = trigger_of("weekly_top_place", place_max=10)
    assert t.accepts(MetricUpdate(metrics.WEEKLY_TOP_PLACE, 1, place=10))
    assert not t.accepts(MetricUpdate(metrics.WEEKLY_TOP_PLACE, 1, place=11))
    assert not t.accepts(MetricUpdate(metrics.WEEKLY_TOP_PLACE, 1))


def test_item_equipped_matches_slot_or_item() -> None:
    by_slot = trigger_of("item_equipped", slot="frame")
    assert by_slot.accepts(MetricUpdate(metrics.ITEM_EQUIPPED, 1, slot="frame", item_key="k"))
    assert not by_slot.accepts(MetricUpdate(metrics.ITEM_EQUIPPED, 1, slot="badge", item_key="k"))
    by_item = trigger_of("item_equipped", item_key="k")
    assert by_item.accepts(MetricUpdate(metrics.ITEM_EQUIPPED, 1, slot="s", item_key="k"))
    assert not by_item.accepts(MetricUpdate(metrics.ITEM_EQUIPPED, 1, slot="s", item_key="o"))


def test_collection_played_without_keys_accepts_any_collection() -> None:
    for params in ({}, {"collection_keys": []}):
        t = trigger_of("collection_played", **params)
        assert t.accepts(MetricUpdate(metrics.COLLECTION_PLAYED, 1, collection_id=COLL_B))
        assert t.accepts(MetricUpdate(metrics.COLLECTION_PLAYED, 1))


def test_collection_played_with_keys_accepts_only_listed() -> None:
    t = trigger_of("collection_played", collection_keys=[COLL_A])
    assert t.accepts(MetricUpdate(metrics.COLLECTION_PLAYED, 1, collection_id=COLL_A))
    assert not t.accepts(MetricUpdate(metrics.COLLECTION_PLAYED, 1, collection_id=COLL_B))
    assert not t.accepts(MetricUpdate(metrics.COLLECTION_PLAYED, 1))


def test_ui_spot_never_accepts_metrics() -> None:
    spot = parse_definition(sample()).finds[0].trigger
    assert not spot.accepts(MetricUpdate(metrics.ROUNDS_PLAYED, 1))


def test_period_key_is_day_or_event() -> None:
    day = date(2026, 11, 3)
    assert trigger_of("rounds_played").period_key(day) == "2026-11-03"
    event_trigger = parse_definition(sample()).finds[1].trigger
    assert event_trigger.period_key(day) == EVENT_PERIOD_KEY


def test_advance_counter_adds_and_level_takes_max_both_capped() -> None:
    counter = trigger_of("rounds_played")
    assert counter.advance(1, MetricUpdate(metrics.ROUNDS_PLAYED, 1)) == 2
    assert counter.advance(2, MetricUpdate(metrics.ROUNDS_PLAYED, 10)) == 3
    level = trigger_of("daily_streak")
    assert level.advance(2, MetricUpdate(metrics.DAILY_STREAK, 1)) == 2
    assert level.advance(2, MetricUpdate(metrics.DAILY_STREAK, 9)) == 3


def test_final_find_is_locked_until_others_unlocked() -> None:
    definition = parse_definition(sample())
    a, b, z = definition.finds
    assert find_state(a, set(), definition) is FindState.OPEN
    assert find_state(z, {"a"}, definition) is FindState.LOCKED
    assert not others_done(definition, {"a"})
    assert others_done(definition, {"a", "b"})
    assert find_state(z, {"a", "b"}, definition) is FindState.OPEN
    assert find_state(z, {"a", "b", "z"}, definition) is FindState.FOUND
    assert find_state(b, {"b"}, definition) is FindState.FOUND


@given(
    start=st.integers(min_value=0, max_value=50),
    amounts=st.lists(st.integers(min_value=0, max_value=20), max_size=15),
)
def test_counter_progress_is_monotonic_and_capped(start: int, amounts: list[int]) -> None:
    trigger = trigger_of("rounds_played")
    assert trigger.target is not None
    progress = min(start, trigger.target)
    for amount in amounts:
        after = trigger.advance(progress, MetricUpdate(metrics.ROUNDS_PLAYED, amount))
        assert progress <= after <= trigger.target
        progress = after


def test_registry_event_only_metrics_are_not_for_quests() -> None:
    assert metrics.is_known(metrics.WEEKLY_TOP_PLACE)
    assert metrics.is_known(metrics.DUEL_WIN)
    assert not metrics.is_config_metric(metrics.WEEKLY_TOP_PLACE)
    assert not metrics.is_config_metric(metrics.ITEM_EQUIPPED)
    assert metrics.is_config_metric(metrics.DUEL_WIN)
    assert metrics.is_config_metric(metrics.WHEEL_SPIN_STREAK)
    assert metrics.merge(2, MetricUpdate(metrics.WHEEL_SPIN_STREAK, 1)) == 2


def test_spin_streak_counts_consecutive_days_ending_today() -> None:
    today = date(2026, 11, 5)
    days = {today, today - timedelta(days=1), today - timedelta(days=2), today - timedelta(days=4)}
    assert wheel_domain.spin_streak(days, today) == 3
    assert wheel_domain.spin_streak(days - {today}, today) == 0
    assert wheel_domain.spin_streak(set(), today) == 0


def test_claim_limit_setting_is_parsed_and_validated() -> None:
    from progression_service.settings import ProgressionSettings
    from pydantic import ValidationError

    assert ProgressionSettings().event_claim_rate == (30, 60)
    assert ProgressionSettings(event_claim_limit="5/10").event_claim_rate == (5, 10)
    for bad in ("0/60", "5", "5/0", "a/b", "5/60/1"):
        with pytest.raises(ValidationError):
            ProgressionSettings(event_claim_limit=bad)


def test_event_item_cannot_be_sold_or_unlocked_by_level() -> None:
    from progression_service.domain import shop
    from progression_service.domain.shop import ItemEventExclusiveError, ItemUnlockRequiredError

    class Cfg:
        def slot(self, key: str) -> object:
            return object()

        def rarity(self, key: str) -> object:
            return object()

    kwargs: Any = {"slot": "frame", "rarity": "rare", "shop_cfg": Cfg()}
    shop.validate_item_fields(level_required=None, price=None, event_key="winter", **kwargs)
    with pytest.raises(ItemEventExclusiveError):
        shop.validate_item_fields(level_required=None, price=5, event_key="winter", **kwargs)
    with pytest.raises(ItemEventExclusiveError):
        shop.validate_item_fields(level_required=2, price=None, event_key="winter", **kwargs)
    with pytest.raises(ItemUnlockRequiredError):
        shop.validate_item_fields(level_required=None, price=None, **kwargs)


def _played(*tags: str, cid: str | None = COLL_A) -> MetricUpdate:
    return MetricUpdate(metrics.COLLECTION_PLAYED, 1, collection_id=cid, collection_tags=tags)


def test_collection_tags_accept_any_matching_tag() -> None:
    t = trigger_of("collection_played", collection_tags=["new_year", "family"])
    assert t.accepts(_played("rock", "family"))
    assert t.accepts(_played("new_year"))
    assert not t.accepts(_played("rock"))


def test_collection_tags_reject_event_without_tags() -> None:
    """Событие старого формата (без тегов) не проходит фильтр по тегу."""
    t = trigger_of("collection_played", collection_tags=["new_year"])
    assert not t.accepts(_played())
    assert not t.accepts(MetricUpdate(metrics.COLLECTION_PLAYED, 1))


def test_empty_collection_tags_is_no_filter() -> None:
    for params in ({}, {"collection_tags": []}):
        t = trigger_of("collection_played", **params)
        assert t.accepts(_played())
        assert t.accepts(_played("rock"))


def test_keys_and_tags_must_both_pass() -> None:
    """ADR 0067: заданные collection_keys и collection_tags сочетаются как «и»."""
    t = trigger_of("collection_played", collection_keys=[COLL_A], collection_tags=["new_year"])
    assert t.accepts(_played("new_year", cid=COLL_A))
    assert not t.accepts(_played("new_year", cid=COLL_B))
    assert not t.accepts(_played("rock", cid=COLL_A))


@pytest.mark.parametrize(
    "trigger",
    [
        server("rounds_played", 1, collection_tags=["new_year"]),
        server("collection_played", 1, collection_tags=["new_year", "new_year"]),
        server("collection_played", 1, collection_tags=["Bad Tag"]),
        server("collection_played", 1, collection_tags=[""]),
    ],
)
def test_invalid_collection_tags_are_reported(trigger: dict[str, Any]) -> None:
    raw = body(find("a", 1, trigger), find("z", 2, SPOT, final=True))
    errors = definition_errors(parse_definition(raw), slot_keys=None)
    assert any("collection_tags" in e["path"] for e in errors)
