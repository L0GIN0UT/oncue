"""Начисление за соло и дейлик: идемпотентность по предмету, потолок, уровни, события (P1)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from progression_service.application.accrual.common import ConfigUnavailableError
from progression_service.infrastructure.db.tables import config_versions, daily_counters, progress
from sqlalchemy import select, update

from gv_core.ids import uuid7
from gv_messaging import Outcome
from gv_testkit import assert_event_matches_schema

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py
TODAY = date(2026, 10, 23)


def midnight_msk(day: date) -> datetime:
    """Полночь начала ``day`` в Europe/Moscow (UTC+3, без перехода на летнее) в UTC."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(hours=3)


# ------------------------------------------------------------------ соло
async def test_solo_session_accrues_xp_and_verses_once(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    event = progression.game_finished(user, [100, 100, 100])

    assert await progression.deliver(event) is Outcome.PROCESSED
    assert await progression.xp_total(user) == 3 * (20 + 10)
    assert await progression.balance(user) == 3

    # Тот же event_id — отсекает inbox.
    assert await progression.deliver(event) is Outcome.DUPLICATE
    # Тот же предмет (session_id) в другом событии — отсекает UNIQUE(op_id).
    again = progression.game_finished(
        user, [100, 100, 100], session_id=UUID(event.payload["session_id"])
    )
    assert await progression.deliver(again) is Outcome.PROCESSED
    assert await progression.xp_rows(user) == 1
    assert await progression.verse_rows(user) == 1
    assert await progression.xp_total(user) == 90
    assert await progression.balance(user) == 3


async def test_void_rounds_and_guests_give_nothing(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    void_round = {
        "index": 0,
        "media_item_id": str(uuid7()),
        "outcome": "void",
        "level": None,
        "points": 0,
        "elapsed_ms": None,
    }
    void_only = progression.game_finished(user, [], rounds=[void_round])
    await progression.deliver(void_only)
    await progression.deliver(progression.game_finished(None, [100, 100]))
    assert await progression.xp_rows() == 0
    assert await progression.verse_rows() == 0
    assert await progression.level(user) == 0  # строка прогресса не создавалась


async def test_daily_rounds_give_no_xp_and_reward_is_not_doubled(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    sid = uuid7()
    # Раунды дейлика (GameFinished с day) опыта не дают (R2).
    await progression.deliver(progression.game_finished(user, [100] * 5, day=TODAY, session_id=sid))
    assert await progression.xp_rows() == 0
    assert await progression.verse_rows() == 0

    # Награда — только по DailyCompleted: 50 + 10 * серия(1) и 5 Версов.
    await progression.deliver(progression.daily_completed(user, TODAY, session_id=sid))
    assert await progression.xp_total(user) == 60
    assert await progression.balance(user) == 5
    # Повтор и другой event_id того же дня — без изменений.
    await progression.deliver(progression.daily_completed(user, TODAY, session_id=sid))
    await progression.deliver(progression.game_finished(user, [100] * 5, day=TODAY, session_id=sid))
    assert await progression.xp_rows(user) == 1
    assert await progression.verse_rows(user) == 1


async def test_daily_streak_grows_per_domain_and_resets_after_gap(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    for offset, expected in [(0, 60), (1, 70), (3, 60)]:  # пропуск дня сбрасывает серию
        before = await progression.xp_total(user)
        await progression.deliver(progression.daily_completed(user, TODAY + timedelta(days=offset)))
        assert await progression.xp_total(user) - before == expected
    # Другой домен ведёт свою серию.
    before = await progression.xp_total(user)
    await progression.deliver(progression.daily_completed(user, TODAY, domain="film"))
    assert await progression.xp_total(user) - before == 60

    response = await progression.client.get("/v1/me/progress", headers=progression.user(user))
    streaks = {s["domain"]: s for s in response.json()["streaks"]}
    assert (streaks["music"]["current"], streaks["music"]["best"]) == (1, 2)
    assert streaks["film"]["current"] == 1


async def test_late_daily_does_not_touch_streak_and_pays_as_streak_of_one(
    progression: Service,
) -> None:
    await progression.publish_config()
    user = uuid7()
    for offset in (0, 1, 2):  # серия 3 к последнему дню
        await progression.deliver(progression.daily_completed(user, TODAY + timedelta(days=offset)))
    before = await progression.xp_total(user)

    late_day = TODAY - timedelta(days=3)
    await progression.deliver(progression.daily_completed(user, late_day))
    # Награда как за серию 1 (50 + 10), а не бонус серии последнего дня (50 + 30).
    assert await progression.xp_total(user) - before == 60
    assert await progression.xp_rows(user) == 4
    # Повтор того же дня — идемпотентно по дню события.
    await progression.deliver(progression.daily_completed(user, late_day))
    assert await progression.xp_rows(user) == 4

    response = await progression.client.get("/v1/me/progress", headers=progression.user(user))
    streak = {s["domain"]: s for s in response.json()["streaks"]}["music"]
    assert (streak["current"], streak["best"]) == (3, 3)
    assert streak["last_day"] == (TODAY + timedelta(days=2)).isoformat()


# ------------------------------------------------------------------ потолок
async def test_solo_daily_cap_limits_xp_and_verses(progression: Service) -> None:
    await progression.publish_config(xp__solo_daily_cap=200, verses__solo_daily_cap=4)
    user = uuid7()
    for _ in range(4):
        await progression.deliver(progression.game_finished(user, [100, 100, 100]))
    assert await progression.xp_total(user) == 200
    assert await progression.balance(user) == 4
    # Сверх потолка проводок нет вовсе.
    assert await progression.xp_rows(user) == 3


async def test_parallel_sessions_cannot_exceed_daily_cap(progression: Service) -> None:
    await progression.publish_config(xp__solo_daily_cap=200, verses__solo_daily_cap=4)
    user = uuid7()
    events = [progression.game_finished(user, [100, 100, 100]) for _ in range(8)]
    await asyncio.gather(*(progression.deliver(e) for e in events))
    assert await progression.xp_total(user) == 200
    assert await progression.balance(user) == 4


async def test_cap_is_per_local_day_and_parallel_sessions_around_midnight(
    progression: Service,
) -> None:
    await progression.publish_config(xp__solo_daily_cap=100)
    user = uuid7()
    midnight = midnight_msk(TODAY + timedelta(days=1))  # 00:00 24 октября по Москве
    events = [
        progression.game_finished(user, [100, 100, 100], finished_at=midnight + delta)
        for delta in [timedelta(seconds=-s) for s in (1, 2, 3, 4)]
        + [timedelta(seconds=s) for s in (0, 1, 2, 3)]
    ]
    await asyncio.gather(*(progression.deliver(e) for e in events))

    assert progression.infra.sessions is not None
    async with progression.infra.sessions() as session:
        rows = (await session.execute(select(daily_counters.c.day, daily_counters.c.solo_xp))).all()
    assert {r.day: r.solo_xp for r in rows} == {TODAY: 100, TODAY + timedelta(days=1): 100}
    assert await progression.xp_total(user) == 200


# ------------------------------------------------------------------ нулевая отметка предмета
async def test_exhausted_cap_marks_item_and_replay_after_raised_cap_pays_nothing(
    progression: Service,
) -> None:
    await progression.publish_config(xp__solo_daily_cap=0, verses__solo_daily_cap=0)
    user, session_id = uuid7(), uuid7()
    first = progression.game_finished(user, [100, 100], session_id=session_id)
    assert await progression.deliver(first) is Outcome.PROCESSED
    assert await progression.xp_rows(user) == 0
    assert await progression.verse_rows(user) == 0
    assert await progression.mark_rows(user) == 2  # опыт и Версы предмета

    later = progression.clock.now() + timedelta(hours=1)
    await progression.publish_config(
        version=2, at=later, xp__solo_daily_cap=5000, verses__solo_daily_cap=500
    )
    replay = progression.game_finished(user, [100, 100], session_id=session_id, finished_at=later)
    assert replay.event_id != first.event_id
    assert await progression.deliver(replay) is Outcome.PROCESSED

    assert await progression.xp_rows(user) == 0
    assert await progression.verse_rows(user) == 0
    assert await progression.mark_rows(user) == 2
    # Другая сессия под новым потолком платится как обычно.
    await progression.deliver(progression.game_finished(user, [100, 100], finished_at=later))
    assert await progression.xp_total(user) > 0


async def test_regular_path_writes_no_marks(progression: Service) -> None:
    await progression.publish_config(xp__solo_daily_cap=100, verses__solo_daily_cap=4)
    user = uuid7()
    await progression.deliver(progression.game_finished(user, [100, 100, 100]))
    await progression.deliver(progression.game_finished(user, [100, 100, 100]))  # частично
    assert await progression.mark_rows() == 0
    # Следующая сессия упирается в потолок целиком: появляются отметки.
    await progression.deliver(progression.game_finished(user, [100, 100, 100]))
    assert await progression.mark_rows(user) == 2


# ------------------------------------------------------------------ уровни и события
async def test_level_up_publishes_events_and_level_never_drops(progression: Service) -> None:
    await progression.publish_config(levels__base=100, levels__step=0)  # 100 опыта на уровень
    user = uuid7()
    await progression.deliver(progression.game_finished(user, [100, 100, 100]))  # 90
    assert await progression.level(user) == 1
    await progression.deliver(progression.game_finished(user, [100, 100, 100]))  # 180
    assert await progression.level(user) == 2

    gained = await progression.outbox("progression.ExperienceGained")
    changed = await progression.outbox("progression.ProgressionLevelChanged")
    assert [e.payload["total_xp"] for e in gained] == [90, 180]
    assert len(changed) == 1
    assert (changed[0].payload["old_level"], changed[0].payload["new_level"]) == (1, 2)
    for event in [*gained, *changed, *await progression.outbox("progression.CurrencyCredited")]:
        assert_event_matches_schema(event)
        assert event.aggregate_id == str(user)
        assert event.topic == "progression.progression.v1"

    # Новая версия с более высокими порогами не отнимает уровень.
    await progression.publish_config(version=2, levels__base=100000, levels__step=0)
    response = await progression.client.get("/v1/me/progress", headers=progression.user(user))
    assert response.json()["level"] == 2


async def _two_versions(progression: Service) -> datetime:
    """Версия 1 с начала суток, версия 2 (больше опыта) через час; вернуть момент смены."""
    start = progression.clock.now()
    change = start + timedelta(hours=1)
    await progression.publish_config(at=start)
    await progression.publish_config(
        version=2, at=change, xp__solo_round__base=100, xp__solo_round__max=200
    )
    return change


async def test_config_version_is_chosen_by_finished_at_not_by_processing_time(
    progression: Service,
) -> None:
    change = await _two_versions(progression)
    user = uuid7()
    # Обе доставки — уже после смены конфига, но по времени события выбирается версия.
    late_old = progression.game_finished(
        user, [100, 100, 100], finished_at=change - timedelta(minutes=1)
    )
    new = progression.game_finished(
        user, [100, 100, 100], finished_at=change + timedelta(minutes=1)
    )
    await progression.deliver(new)
    await progression.deliver(late_old)

    gained = {
        e.payload["config_version"]: e.payload["amount"]
        for e in await progression.outbox("progression.ExperienceGained")
    }
    assert gained == {1: 90, 2: 330}
    assert await progression.xp_total(user) == 420


async def test_event_before_first_publication_uses_earliest_version(progression: Service) -> None:
    change = await _two_versions(progression)
    user = uuid7()
    await progression.deliver(
        progression.game_finished(user, [100, 100, 100], finished_at=change - timedelta(days=30))
    )
    gained = await progression.outbox("progression.ExperienceGained")
    assert [(e.payload["config_version"], e.payload["amount"]) for e in gained] == [(1, 90)]


async def test_match_uses_version_at_finished_at(progression: Service) -> None:
    change = await _two_versions(progression)
    await progression.publish_config(
        version=3, at=change + timedelta(hours=1), verses__duel__win=99, verses__duel__loss=7
    )
    winner, loser = uuid7(), uuid7()
    await progression.deliver(
        progression.duel(winner, loser, finished_at=change + timedelta(minutes=5))
    )
    versions = {
        e.payload["config_version"]
        for e in await progression.outbox("progression.CurrencyCredited")
    }
    assert versions == {2}
    assert await progression.balance(winner) == 10  # версия 2: сетка дуэли как в примере


async def test_daily_and_weekly_use_event_time_from_envelope(progression: Service) -> None:
    change = await _two_versions(progression)
    user = uuid7()
    old = change - timedelta(minutes=1)
    daily = progression.daily_completed(user, TODAY)
    await progression.deliver(daily.model_copy(update={"occurred_at": old}))
    weekly = progression.weekly_top([(user, 1)])
    await progression.deliver(weekly.model_copy(update={"occurred_at": old}))
    versions = {
        e.payload["config_version"]
        for e in await progression.outbox("progression.CurrencyCredited")
    }
    assert versions == {1}


async def test_events_are_published_in_the_same_transaction_as_the_ledger(
    progression: Service,
) -> None:
    await progression.publish_config()
    user = uuid7()
    await progression.deliver(progression.game_finished(user, [100]))
    credited = await progression.outbox("progression.CurrencyCredited")
    assert len(credited) == 1
    payload = credited[0].payload
    assert (payload["amount"], payload["balance_after"], payload["bucket"]) == (1, 1, "earned")
    assert payload["actor_id"] is None
    assert payload["op_id"].endswith(":verse")


# ------------------------------------------------------------------ конфиг и контракт
async def test_missing_config_defers_event_instead_of_losing_it(progression: Service) -> None:
    user = uuid7()
    event = progression.game_finished(user, [100, 100])

    assert await progression.deliver(event) is Outcome.DEAD
    assert progression.dead_letters.letters[-1]["reason"] == "retries_exhausted"
    assert await progression.xp_rows() == 0

    await progression.publish_config()
    # Та же доставка после публикации конфига проходит: отметка inbox не осталась.
    assert await progression.deliver(event) is Outcome.PROCESSED
    assert await progression.xp_total(user) == 2 * 30


async def test_use_case_raises_retryable_error_without_config(progression: Service) -> None:
    from progression_service.application.accrual.solo import SoloSession

    assert progression.infra.sessions is not None
    async with progression.infra.sessions() as session, session.begin():
        use_case = progression.container.accrue_solo(session)
        with pytest.raises(ConfigUnavailableError) as exc:
            await use_case.execute(
                SoloSession(
                    session_id=uuid7(),
                    user_id=uuid7(),
                    day=None,
                    finished_at=progression.clock.now(),
                    round_points=(1,),
                )
            )
    assert exc.value.code == "progression.config_unavailable"


async def test_contract_violation_goes_to_dlq_without_retries(progression: Service) -> None:
    await progression.publish_config()
    event = progression.game_finished(uuid7(), [100])
    broken = event.model_copy(update={"payload": {**event.payload, "rounds": "oops"}})
    assert await progression.deliver(broken) is Outcome.DEAD
    assert progression.dead_letters.letters[-1]["reason"] == "permanent"


async def test_invalid_published_config_is_retryable_and_logged_not_dlq_permanent(
    progression: Service,
    make_config: Any,
) -> None:
    """Битый конфиг — состояние сервиса: повтор и ERROR-лог, а не вечная потеря события."""
    from structlog.testing import capture_logs

    await progression.publish_config()
    assert progression.infra.sessions is not None
    async with progression.infra.sessions() as session, session.begin():
        await session.execute(
            update(config_versions).values(body={**make_config(), "min_rounds_ratio": 7})
        )
    user = uuid7()
    event = progression.game_finished(user, [100])
    with capture_logs() as logs:
        assert await progression.deliver(event) is Outcome.DEAD
    assert progression.dead_letters.letters[-1]["reason"] == "retries_exhausted"
    errors = [e for e in logs if e["event"] == "progression.config_invalid_published"]
    assert errors
    assert all(e["log_level"] == "error" for e in errors)
    assert await progression.xp_rows() == 0

    # После исправления конфига то же событие обрабатывается: отметка inbox не осталась.
    async with progression.infra.sessions() as session, session.begin():
        await session.execute(update(config_versions).values(body=make_config()))
    assert await progression.deliver(event) is Outcome.PROCESSED
    assert await progression.xp_total(user) == 30


async def test_progress_row_remembers_config_version(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    await progression.deliver(progression.game_finished(user, [100]))
    await progression.publish_config(version=2)
    await progression.deliver(progression.game_finished(user, [100]))
    assert progression.infra.sessions is not None
    async with progression.infra.sessions() as session:
        version = await session.scalar(
            select(progress.c.config_version).where(progress.c.user_id == user)
        )
    assert version == 2


async def test_guest_flag_blocks_accrual_even_with_user_id(progression: Service) -> None:
    """Второй барьер: ``is_guest`` в payload важнее ``user_id``."""
    await progression.publish_config()
    user = uuid7()
    event = progression.game_finished(user, [100, 100])
    guest = event.model_copy(update={"payload": {**event.payload, "is_guest": True}})
    assert await progression.deliver(guest) is Outcome.PROCESSED
    assert await progression.xp_rows() == 0
    assert await progression.verse_rows() == 0
    assert await progression.level(user) == 0
