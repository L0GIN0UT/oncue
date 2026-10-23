"""Связка обработчиков P1 и P2: общая транзакция, откат, повтор, одинаковый разбор payload."""

from __future__ import annotations

from typing import Any

import pytest
from progression_service.application.quests.tracker import ProgressTracker

from gv_core.ids import uuid7
from gv_messaging import Outcome

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py


class FlakyTracker(ProgressTracker):
    """Настоящий трекер, который падает первые ``failures`` вызовов (сбой зависимости)."""

    def __init__(self, *args: Any, budget: dict[str, int], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.budget = budget  # общий на все экземпляры: трекер создаётся на каждое событие

    async def on_game_finished(self, game: Any) -> bool:
        if self.budget["left"] > 0:
            self.budget["left"] -= 1
            raise ConnectionError("сбой P2")
        return await super().on_game_finished(game)


def break_tracker(progression: Service, failures: int) -> None:
    container = progression.container

    def tracker(session: Any) -> FlakyTracker:
        return FlakyTracker(container.uow_factory(session), container.infra.clock, budget=budget)

    budget = {"left": failures}
    container.tracker = tracker  # type: ignore[method-assign]  # подмена зависимости в тесте


async def test_p2_failure_rolls_back_p1_accrual_and_event_is_retried(progression: Service) -> None:
    await progression.publish_config(rewards=True)
    user = uuid7()
    break_tracker(progression, failures=1)
    outcome = await progression.deliver(progression.game_finished(user, [100, 100, 100]))

    # первая попытка упала целиком (P1 откатился), повтор прошёл: начисление ровно одно
    assert outcome is Outcome.PROCESSED
    assert await progression.xp_total(user) == 30 * 3 + 30  # 3 раунда + награда play_3_solo
    assert len(await progression.rows("xp_ledger", user, reason="solo_round")) == 1
    assert len(await progression.rows("xp_ledger", user, reason="quest")) == 1
    assert progression.dead_letters.letters == []


async def test_persistent_p2_failure_does_not_leave_half_applied_p1_accrual(
    progression: Service,
) -> None:
    await progression.publish_config(rewards=True)
    user = uuid7()
    break_tracker(progression, failures=100)
    event = progression.game_finished(user, [100])
    outcome = await progression.deliver(event)

    assert outcome is Outcome.DEAD  # повторы исчерпаны: сообщение в DLQ, а не потеряно молча
    assert progression.dead_letters.letters[-1]["reason"] != "permanent"
    assert await progression.xp_rows(user) == 0  # P1 откатился вместе с P2
    assert await progression.rows("inbox") == []  # отметки inbox нет: событие можно повторить


async def test_contract_violation_is_permanent_for_both_and_posts_nothing(
    progression: Service,
) -> None:
    await progression.publish_config(rewards=True)
    user = uuid7()
    event = progression.game_finished(user, [100])
    del event.payload["domain"]
    assert await progression.deliver(event) is Outcome.DEAD
    assert progression.dead_letters.letters[-1]["reason"] == "permanent"
    assert await progression.xp_rows(user) == 0
    assert await progression.rows("metric_counters", user) == []


async def test_what_p1_accepts_p2_accepts(progression: Service) -> None:
    """Раунды с любыми допустимыми исходами и дейлик: P2 не даёт PermanentError за P1."""
    await progression.publish_config(rewards=True)
    user = uuid7()
    rounds = [
        {**r, "outcome": o, "points": 0 if o != "exact" else 10, "index": i}
        for i, (r, o) in enumerate(
            zip(
                progression.game_finished(user, [0] * 5).payload["rounds"],
                ["exact", "partial", "miss", "gave_up", "void"],
                strict=True,
            )
        )
    ]
    assert (
        await progression.deliver(progression.game_finished(user, [], rounds=rounds))
        is Outcome.PROCESSED
    )
    (played,) = await progression.rows("metric_counters", user, metric="rounds_played")
    (correct,) = await progression.rows("metric_counters", user, metric="correct_answers")
    assert (played.value, correct.value) == (4, 1)  # void не считается
    assert progression.dead_letters.letters == []
