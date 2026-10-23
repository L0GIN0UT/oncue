"""Публичные ручки прогресса и кошелька (P1) и сверка журнала с балансом."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from progression_service.infrastructure.db.tables import balances, progress
from progression_service.infrastructure.jobs import RECONCILE_JOB
from prometheus_client import REGISTRY
from sqlalchemy import update

from gv_core.ids import uuid7
from gv_service import JobResult
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py


async def test_my_progress_after_solo_session(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    await progression.deliver(progression.game_finished(user, [100, 100, 100]))

    response = await progression.client.get("/v1/me/progress", headers=progression.user(user))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    body = response.json()
    assert (body["level"], body["max_level"], body["total_xp"]) == (1, 100, 90)
    assert (body["xp_in_level"], body["xp_to_next"]) == (90, 540 - 90)
    assert (body["solo_xp_today"], body["solo_xp_cap"]) == (90, 2000)
    assert body["boost"] is None
    assert body["config_version"] == 1


async def test_my_progress_for_newcomer_is_level_one(progression: Service) -> None:
    await progression.publish_config()
    response = await progression.client.get("/v1/me/progress", headers=progression.user())
    body = response.json()
    assert (body["level"], body["total_xp"], body["streaks"]) == (1, 0, [])


async def test_solo_xp_today_resets_on_the_next_local_day(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    await progression.deliver(progression.game_finished(user, [100, 100, 100]))
    progression.clock.advance(timedelta(days=1))
    response = await progression.client.get("/v1/me/progress", headers=progression.user(user))
    assert response.json()["solo_xp_today"] == 0


async def test_my_wallet_lists_balance_and_recent_entries(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    first = progression.game_finished(user, [100, 100, 100])
    await progression.deliver(first)
    progression.clock.advance(timedelta(minutes=5))
    await progression.deliver(progression.weekly_top([(user, 1)]))

    response = await progression.client.get("/v1/me/wallet", headers=progression.user(user))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    body = response.json()
    assert (body["currency"], body["earned"], body["paid"], body["total"]) == (
        "verses",
        503,
        0,
        503,
    )
    assert [(e["reason"], e["amount"], e["direction"]) for e in body["recent"]] == [
        ("weekly_top", 500, "credit"),
        ("solo_round", 3, "credit"),
    ]


async def test_wallet_of_newcomer_is_empty(progression: Service) -> None:
    response = await progression.client.get("/v1/me/wallet", headers=progression.user())
    assert response.json() == {
        "currency": "verses",
        "earned": 0,
        "paid": 0,
        "total": 0,
        "recent": [],
    }


async def test_public_progress_of_another_player(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    await progression.deliver(progression.game_finished(user, [100, 100, 100]))
    response = await progression.client.get(
        f"/v1/users/{user}/progress", headers=progression.user()
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"user_id": str(user), "level": 1, "total_xp": 90, "badges": []}


async def test_public_progress_unknown_and_hidden_user_is_404(progression: Service) -> None:
    await progression.publish_config()
    unknown = await progression.client.get(
        f"/v1/users/{uuid7()}/progress", headers=progression.user()
    )
    assert_problem(unknown, 404, "progression.user_not_found")

    user = uuid7()
    await progression.deliver(progression.game_finished(user, [100]))
    assert progression.infra.sessions is not None
    async with progression.infra.sessions() as session, session.begin():
        await session.execute(
            update(progress).where(progress.c.user_id == user).values(hidden=True)
        )
    hidden = await progression.client.get(f"/v1/users/{user}/progress", headers=progression.user())
    assert_problem(hidden, 404, "progression.user_not_found")


@pytest.mark.parametrize(
    "path", ["/v1/me/progress", "/v1/me/wallet", f"/v1/users/{uuid7()}/progress"]
)
async def test_guest_and_anonymous_are_rejected(progression: Service, path: str) -> None:
    assert_problem(await progression.client.get(path), 401, "auth.unauthenticated")
    guest = {"Authorization": f"Bearer {progression.issuer.issue_guest()}"}
    assert_problem(await progression.client.get(path, headers=guest), 403, "auth.guest_forbidden")


async def test_my_progress_without_published_config_is_503(progression: Service) -> None:
    response = await progression.client.get("/v1/me/progress", headers=progression.user())
    assert_problem(response, 503, "progression.config_unavailable")


# ------------------------------------------------------------------ метрики и сверка
def _sample(name: str, labels: dict[str, str] | None = None) -> float | None:
    return REGISTRY.get_sample_value(name, labels or {})


async def test_consumer_lag_metric_is_set_from_event_age(progression: Service) -> None:
    await progression.publish_config()
    event = progression.game_finished(uuid7(), [100])
    old = event.model_copy(update={"occurred_at": progression.clock.now() - timedelta(seconds=7)})
    await progression.deliver(old)
    assert _sample("gv_progression_consumer_lag", {"event_type": "game.GameFinished"}) == 7.0


async def test_reconcile_is_quiet_on_consistent_data(progression: Service) -> None:
    await progression.publish_config()
    await progression.deliver(progression.game_finished(uuid7(), [100, 100]))
    await progression.deliver(progression.duel(uuid7(), uuid7()))
    assert await progression.run_job(RECONCILE_JOB) is JobResult.OK
    assert _sample("gv_progression_ledger_mismatches") == 0.0


async def test_reconcile_detects_manually_corrupted_balance_and_xp(progression: Service) -> None:
    await progression.publish_config()
    rich, learner = uuid7(), uuid7()
    await progression.deliver(progression.weekly_top([(rich, 1)]))
    await progression.deliver(progression.game_finished(learner, [100, 100]))
    assert progression.infra.sessions is not None
    async with progression.infra.sessions() as session, session.begin():
        await session.execute(
            update(balances).where(balances.c.user_id == rich).values(amount=balances.c.amount + 7)
        )
        await session.execute(
            update(progress)
            .where(progress.c.user_id == learner)
            .values(total_xp=progress.c.total_xp - 1)
        )

    assert await progression.run_job(RECONCILE_JOB) is JobResult.OK
    assert _sample("gv_progression_ledger_mismatches") == 2.0

    async with progression.container.uow_factory()() as uow:
        found = {
            (m.kind, m.user_id): (m.ledger_sum, m.stored) for m in await uow.ledger.mismatches(10)
        }
    assert found == {("currency", rich): (500, 507), ("xp", learner): (60, 59)}


async def test_public_progress_is_privately_cacheable_for_a_minute(progression: Service) -> None:
    await progression.publish_config()
    user = uuid7()
    await progression.deliver(progression.game_finished(user, [100]))
    response = await progression.client.get(
        f"/v1/users/{user}/progress", headers=progression.user()
    )
    assert response.headers["cache-control"] == "private, max-age=60"


async def test_my_progress_reports_the_effective_config_version(progression: Service) -> None:
    """``config_version`` — версия, по которой считаются пороги, а не версия строки прогресса."""
    await progression.publish_config()
    user = uuid7()
    await progression.deliver(progression.game_finished(user, [100]))
    await progression.publish_config(version=2, levels__base=1000)

    body = (await progression.client.get("/v1/me/progress", headers=progression.user(user))).json()
    assert body["config_version"] == 2
    assert body["xp_to_next"] == 1000 + 20 * 2 - 30  # пороги версии 2
