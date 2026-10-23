"""Домен заданий: периоды, детерминированное назначение, прогресс, метрики."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from progression_service.domain import quests
from progression_service.domain.config import QuestDef, Reward
from progression_service.domain.metrics import MetricUpdate, merge

pytestmark = pytest.mark.unit

USER = UUID(int=7)


def quest(key: str, weight: int = 1, metric: str = "rounds_played", **kw: object) -> QuestDef:
    return QuestDef(
        key=key,
        period="daily",
        title=key,
        description="",
        metric=metric,
        target=int(kw.get("target", 3)),  # type: ignore[call-overload]  # тестовый хелпер
        reward=Reward(10, 1),
        enabled=True,
        weight=weight,
        domain=kw.get("domain"),  # type: ignore[arg-type]  # тестовый хелпер
    )


POOL = [quest(f"q{i}", weight=i + 1) for i in range(6)]


def test_period_start_daily_weekly_and_one_time() -> None:
    friday = date(2026, 10, 23)
    assert quests.period_start("daily", friday) == friday
    assert quests.period_start("weekly", friday) == date(2026, 10, 19)  # понедельник
    assert quests.period_start("weekly", date(2026, 10, 19)) == date(2026, 10, 19)
    assert quests.period_start("weekly", date(2026, 10, 25)) == date(2026, 10, 19)  # воскресенье
    assert quests.period_end("weekly", date(2026, 10, 19)) == date(2026, 10, 25)
    assert quests.period_end("daily", friday) == friday
    assert quests.period_end("one_time", friday) is None


def test_selection_is_deterministic_and_has_no_repeats() -> None:
    day = date(2026, 10, 23)
    args = {"user_id": USER, "period": "daily", "start": day, "version": 1}
    first = quests.select_quests(POOL, 3, **args)
    assert first == quests.select_quests(POOL, 3, **args)
    assert len({q.key for q in first}) == 3
    # порядок пула сохраняется
    assert [q.key for q in first] == sorted(q.key for q in first)


def test_selection_varies_with_user_day_and_version() -> None:
    sets = {
        tuple(
            q.key
            for q in quests.select_quests(POOL, 3, user_id=u, period="daily", start=d, version=v)
        )
        for u in (UUID(int=1), UUID(int=2), UUID(int=3))
        for d in (date(2026, 10, 23), date(2026, 10, 24))
        for v in (1, 2)
    }
    assert len(sets) > 1


def test_pool_smaller_than_count_gives_whole_pool() -> None:
    chosen = quests.select_quests(
        POOL[:2], 10, user_id=USER, period="weekly", start=date(2026, 10, 19), version=1
    )
    assert [q.key for q in chosen] == ["q0", "q1"]
    assert (
        quests.select_quests(
            [], 3, user_id=USER, period="daily", start=date(2026, 10, 19), version=1
        )
        == []
    )


def test_first_pick_follows_weights() -> None:
    first = Counter(
        quests.select_quests(
            POOL, 1, user_id=UUID(int=n), period="daily", start=date(2026, 1, 1), version=1
        )[0].key
        for n in range(3000)
    )
    # веса 1..6: q5 (вес 6) выпадает заметно чаще q0 (вес 1)
    assert first["q5"] > 3 * first["q0"]
    assert first["q0"] > 0


@given(
    user=st.uuids(),
    offset=st.integers(0, 3000),
    count=st.integers(0, 8),
    version=st.integers(1, 50),
)
def test_selection_property(user: UUID, offset: int, count: int, version: int) -> None:
    start = date(2026, 1, 1) + timedelta(days=offset)
    args = {"user_id": user, "period": "daily", "start": start, "version": version}
    chosen = quests.select_quests(POOL, count, **args)
    assert chosen == quests.select_quests(POOL, count, **args)
    assert len(chosen) == min(count, len(POOL))
    assert len({q.key for q in chosen}) == len(chosen)


def test_snapshot_keeps_config_values_and_filters_by_metric_and_domain() -> None:
    q = quest("q", metric="correct_answers", target=5, domain="film")
    snap = quests.QuestSnapshot.of(q, date(2026, 10, 23), 4)
    assert (snap.target, snap.reward, snap.config_version) == (5, Reward(10, 1), 4)
    assert snap.accepts(MetricUpdate("correct_answers", 1, "film"))
    assert not snap.accepts(MetricUpdate("correct_answers", 1, "music"))
    assert not snap.accepts(MetricUpdate("correct_answers", 1, None))
    assert not snap.accepts(MetricUpdate("rounds_played", 1, "film"))
    anywhere = quests.QuestSnapshot.of(quest("a", metric="wins"), date(2026, 10, 23), 1)
    assert anywhere.accepts(MetricUpdate("wins", 1, None))


@given(steps=st.lists(st.integers(0, 6), max_size=20))
def test_progress_is_monotonic_and_capped(steps: list[int]) -> None:
    snap = quests.QuestSnapshot.of(quest("q", target=5), date(2026, 10, 23), 1)
    progress = 0
    for step in steps:
        new = snap.advance(progress, MetricUpdate("rounds_played", step))
        assert progress <= new <= snap.target
        progress = new


def test_level_metrics_take_maximum_counters_add() -> None:
    assert merge(3, MetricUpdate("rounds_played", 2)) == 5
    assert merge(7, MetricUpdate("daily_streak", 3)) == 7
    assert merge(7, MetricUpdate("daily_streak", 9)) == 9


def test_unknown_metric_is_rejected() -> None:
    with pytest.raises(ValueError, match="реестр"):
        MetricUpdate("moon_phase", 1)


def test_op_id_is_stable() -> None:
    assert quests.op_id(USER, "q", date(2026, 10, 23)) == f"quest:{USER}:q:2026-10-23"
