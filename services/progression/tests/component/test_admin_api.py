"""Admin API progression (поток P3): права, конфиги, ручная выдача, сторно, журнал."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from progression_service.infrastructure.db.tables import (
    balances,
    config_versions,
    currency_ledger,
)
from progression_service.infrastructure.db.tables_admin import admin_actions
from sqlalchemy import select, update

from gv_core.ids import uuid7
from gv_testkit import assert_event_matches_schema, assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py
REASON = {"reason": "проверка контракта"}
SHA0 = "0" * 64
ADMIN_ID = UUID(int=77)
MANAGE = "progression.config.manage"
PUBLISH = "progression.config.publish"
GRANT = "progression.ledger.grant"
REVERSE = "progression.ledger.reverse"
READ = "progression.ledger.read"
UID = str(uuid7())


def key() -> dict[str, str]:
    return {"Idempotency-Key": f"idem-{uuid7().hex}"}


async def call(
    svc: Service,
    method: str,
    path: str,
    body: Any = None,
    *,
    perm: str = MANAGE,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    token = svc.issuer.issue(user_id=ADMIN_ID, perms=("admin.access", perm))
    sent = {"Authorization": f"Bearer {token}", **(headers if headers is not None else key())}
    return await svc.client.request(method, path, json=body, headers=sent)


async def player(svc: Service) -> UUID:
    """Игрок с прогрессом и 3 Версами (соло-сессия)."""
    user = uuid7()
    await svc.deliver(svc.game_finished(user, [100, 100, 100]))
    return user


async def rows(svc: Service, table: Any) -> list[Any]:
    assert svc.infra.sessions is not None
    async with svc.infra.sessions() as session:
        return list((await session.execute(select(table))).all())


# ------------------------------------------------------------------ права
CASES: list[tuple[str, str, Any, str]] = [
    ("GET", "/admin/v1/configs", None, MANAGE),
    ("GET", "/admin/v1/configs/1", None, MANAGE),
    ("PUT", "/admin/v1/configs/draft", {"config": {}, **REASON}, MANAGE),
    (
        "POST",
        "/admin/v1/configs/draft/publish",
        {"version": 2, "body_sha256": SHA0, **REASON},
        PUBLISH,
    ),
    ("POST", "/admin/v1/configs/1/rollback", REASON, PUBLISH),
    ("GET", f"/admin/v1/users/{UID}/ledger", None, READ),
    ("POST", f"/admin/v1/users/{UID}/ledger/grant", {"amount": 10, **REASON}, GRANT),
    ("POST", "/admin/v1/ledger/op-1/reverse", REASON, REVERSE),
    ("GET", "/admin/v1/quests", None, MANAGE),
    ("GET", "/admin/v1/badges", None, MANAGE),
]
ALL_PERMS = (MANAGE, PUBLISH, GRANT, REVERSE, READ)


@pytest.mark.parametrize("case", CASES, ids=[f"{m} {p}" for m, p, _, _ in CASES])
async def test_admin_needs_admin_access_and_specific_permission(
    progression: Service, case: tuple[str, str, Any, str]
) -> None:
    method, path, body, perm = case
    idem = key()
    anonymous = await progression.client.request(method, path, json=body, headers=idem)
    assert_problem(anonymous, 401, "auth.unauthenticated")
    others = [p for p in ALL_PERMS if p != perm]
    tokens = [
        progression.issuer.issue_guest(),
        progression.issuer.issue(),
        progression.issuer.issue(perms=["admin.access"]),
        progression.issuer.issue(perms=[perm]),
        progression.issuer.issue(perms=["admin.access", *others]),
    ]
    for token in tokens:
        response = await progression.client.request(
            method, path, json=body, headers={**idem, "Authorization": f"Bearer {token}"}
        )
        assert_problem(response, 403, "auth.forbidden")
    allowed = await call(progression, method, path, body, perm=perm)
    assert allowed.status_code not in (401, 403, 501), allowed.text


async def test_mutation_requires_idempotency_key(progression: Service) -> None:
    response = await call(
        progression,
        "POST",
        "/admin/v1/configs/draft/publish",
        {"version": 2, "body_sha256": SHA0, **REASON},
        perm=PUBLISH,
        headers={},
    )
    assert_problem(response, 422, "idempotency.key_required")


async def test_success_responses_are_no_store(progression: Service) -> None:
    await progression.publish_config()
    user = await player(progression)
    ok = [
        await call(progression, "GET", "/admin/v1/configs"),
        await call(progression, "GET", "/admin/v1/configs/1"),
        await call(progression, "GET", "/admin/v1/quests"),
        await call(progression, "GET", "/admin/v1/badges"),
        await call(progression, "GET", f"/admin/v1/users/{user}/ledger", perm=READ),
        await call(
            progression,
            "POST",
            f"/admin/v1/users/{user}/ledger/grant",
            {"amount": 5, **REASON},
            perm=GRANT,
        ),
    ]
    for response in ok:
        assert response.status_code < 300, response.text
        assert response.headers["cache-control"] == "no-store"


# ------------------------------------------------------------------ конфиги
async def test_list_and_get_versions(progression: Service) -> None:
    await progression.publish_config()
    listing = (await call(progression, "GET", "/admin/v1/configs")).json()["items"]
    assert [(v["version"], v["status"]) for v in listing] == [(1, "published")]
    body = (await call(progression, "GET", "/admin/v1/configs/1")).json()
    assert body["config"]["levels"]["base"] == 500
    assert_problem(
        await call(progression, "GET", "/admin/v1/configs/9"),
        404,
        "progression.config_version_not_found",
    )


async def test_draft_publish_archives_previous_and_applies_to_new_events(
    progression: Service, make_config: Callable[..., dict[str, Any]]
) -> None:
    await progression.publish_config()
    await progression.add_item("custom_avatar", slot="avatar", price=10)  # товар из конфига
    # без заданий (P2) в сумме Версов только начисление за раунды
    config = make_config(verses__solo_round=2, quests__daily_count=0, quests__weekly_count=0)
    saved = await call(progression, "PUT", "/admin/v1/configs/draft", {"config": config, **REASON})
    assert saved.status_code == 200, saved.text
    assert (saved.json()["card"]["version"], saved.json()["card"]["status"]) == (2, "draft")
    # повторное сохранение заменяет единственный черновик
    again = await call(progression, "PUT", "/admin/v1/configs/draft", {"config": config, **REASON})
    assert again.json()["card"]["version"] == 2

    published = await call(
        progression,
        "POST",
        "/admin/v1/configs/draft/publish",
        {"version": 2, "body_sha256": again.json()["card"]["body_sha256"], **REASON},
        perm=PUBLISH,
    )
    assert published.status_code == 200, published.text
    assert published.json()["card"]["status"] == "published"
    assert published.json()["card"]["published_at"] is not None
    listing = (await call(progression, "GET", "/admin/v1/configs")).json()["items"]
    assert [(v["version"], v["status"]) for v in listing] == [(2, "published"), (1, "archived")]

    progression.clock.advance(timedelta(minutes=1))
    user = uuid7()
    await progression.deliver(progression.game_finished(user, [100, 100, 100]))
    assert await progression.balance(user) == 6  # 3 раунда по 2 Версы: новая версия


async def test_invalid_draft_is_rejected_with_paths(
    progression: Service, make_config: Callable[..., dict[str, Any]]
) -> None:
    await progression.publish_config()
    broken = make_config(levels__base="много")
    broken["admin"] = {"grant_max_amount": 0}
    response = await call(
        progression, "PUT", "/admin/v1/configs/draft", {"config": broken, **REASON}
    )
    problem = assert_problem(response, 422, "progression.config_invalid")
    errors = problem["details"]["errors"]
    assert {"path", "reason"} <= set(errors[0])
    assert any(e["path"].startswith("admin") for e in errors)
    # черновик не создан
    listing = (await call(progression, "GET", "/admin/v1/configs")).json()["items"]
    assert [v["version"] for v in listing] == [1]


async def test_broken_rewards_blocks_are_rejected_when_saving_draft(
    progression: Service, make_config: Callable[..., dict[str, Any]]
) -> None:
    """Блоки quests/badges/wheel проверяет общий ``ProgressionConfig.parse`` (P2): 422, не 500."""
    await progression.publish_config()
    for patch in (
        {"quests": {"items": [{"key": None}]}},
        {"quests": {"items": [{"key": "a", "metric": "нет_такой"}]}},
        {"wheel": {"sectors": []}},
    ):
        broken = make_config()
        broken.update(patch)
        response = await call(
            progression, "PUT", "/admin/v1/configs/draft", {"config": broken, **REASON}
        )
        assert_problem(response, 422, "progression.config_invalid")
    listing = (await call(progression, "GET", "/admin/v1/configs")).json()["items"]
    assert [v["version"] for v in listing] == [1]


async def test_publish_errors(progression: Service) -> None:
    await progression.publish_config()
    not_draft = await call(
        progression,
        "POST",
        "/admin/v1/configs/draft/publish",
        {"version": 1, "body_sha256": SHA0, **REASON},
        perm=PUBLISH,
    )
    assert_problem(not_draft, 409, "progression.config_not_draft")
    unknown = await call(
        progression,
        "POST",
        "/admin/v1/configs/draft/publish",
        {"version": 7, "body_sha256": SHA0, **REASON},
        perm=PUBLISH,
    )
    assert_problem(unknown, 404, "progression.config_version_not_found")


async def test_rollback_makes_copy_a_draft(
    progression: Service, make_config: Callable[..., dict[str, Any]]
) -> None:
    await progression.publish_config()
    await progression.add_item("custom_avatar", slot="avatar", price=10)
    saved = await call(
        progression,
        "PUT",
        "/admin/v1/configs/draft",
        {"config": make_config(levels__base=900), **REASON},
    )
    await call(
        progression,
        "POST",
        "/admin/v1/configs/draft/publish",
        {"version": 2, "body_sha256": saved.json()["card"]["body_sha256"], **REASON},
        perm=PUBLISH,
    )
    rolled = await call(progression, "POST", "/admin/v1/configs/1/rollback", REASON, perm=PUBLISH)
    assert rolled.status_code == 201, rolled.text
    assert (rolled.json()["card"]["version"], rolled.json()["card"]["status"]) == (3, "draft")
    assert rolled.json()["config"]["levels"]["base"] == 500
    # черновик не публиковался никогда
    assert_problem(
        await call(progression, "POST", "/admin/v1/configs/3/rollback", REASON, perm=PUBLISH),
        409,
        "progression.config_never_published",
    )
    assert_problem(
        await call(progression, "POST", "/admin/v1/configs/99/rollback", REASON, perm=PUBLISH),
        404,
        "progression.config_version_not_found",
    )


async def test_config_mutation_is_idempotent_and_audited(
    progression: Service, make_config: Callable[..., dict[str, Any]]
) -> None:
    await progression.publish_config()
    await progression.add_item("custom_avatar", slot="avatar", price=10)
    saved = await call(
        progression, "PUT", "/admin/v1/configs/draft", {"config": make_config(), **REASON}
    )
    idem = key()
    body = {"version": 2, "body_sha256": saved.json()["card"]["body_sha256"], **REASON}
    first = await call(
        progression, "POST", "/admin/v1/configs/draft/publish", body, perm=PUBLISH, headers=idem
    )
    again = await call(
        progression, "POST", "/admin/v1/configs/draft/publish", body, perm=PUBLISH, headers=idem
    )
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    reused = await call(
        progression,
        "POST",
        "/admin/v1/configs/draft/publish",
        {"version": 3, "body_sha256": SHA0, **REASON},
        perm=PUBLISH,
        headers=idem,
    )
    assert_problem(reused, 409, "idempotency.key_reused")
    actions = [r.action for r in await rows(progression, admin_actions)]
    assert actions.count("config.publish") == 1
    assert "config.save_draft" in actions


# ------------------------------------------------------------------ выдача
async def test_grant_posts_once_emits_event_and_audits(progression: Service) -> None:
    await progression.publish_config()
    user = await player(progression)
    idem = key()
    body = {"amount": 100, "bucket": "earned", **REASON}
    path = f"/admin/v1/users/{user}/ledger/grant"
    first = await call(progression, "POST", path, body, perm=GRANT, headers=idem)
    assert first.status_code == 201, first.text
    payload = first.json()
    assert payload["balance_after"] == 103
    assert payload["entry"]["op_id"] == f"admin:{idem['Idempotency-Key']}"
    assert (payload["entry"]["reason"], payload["entry"]["actor_id"]) == (
        "admin_grant",
        str(ADMIN_ID),
    )
    again = await call(progression, "POST", path, body, perm=GRANT, headers=idem)
    assert again.status_code == 201
    assert again.json() == payload
    assert await progression.balance(user) == 103
    assert await progression.verse_rows(user) == 2  # соло + одна выдача

    credited = [
        e
        for e in await progression.outbox("progression.CurrencyCredited")
        if e.payload["reason"] == "admin_grant"
    ]
    assert len(credited) == 1
    assert credited[0].payload["actor_id"] == str(ADMIN_ID)
    assert_event_matches_schema(credited[0])
    actions = [r for r in await rows(progression, admin_actions) if r.action == "ledger.grant"]
    assert len(actions) == 1
    assert actions[0].actor_id == ADMIN_ID
    assert actions[0].reason == REASON["reason"]

    other_body = {**body, "amount": 101}
    reused = await call(progression, "POST", path, other_body, perm=GRANT, headers=idem)
    assert_problem(reused, 409, "idempotency.key_reused")
    assert await progression.balance(user) == 103


async def test_grant_limits_and_validation(progression: Service) -> None:
    await progression.publish_config()  # max 10000 за операцию, 20000 в сутки, причина от 10
    user = await player(progression)
    path = f"/admin/v1/users/{user}/ledger/grant"

    async def grant(amount: int, reason: str = REASON["reason"]) -> httpx.Response:
        return await call(
            progression, "POST", path, {"amount": amount, "reason": reason}, perm=GRANT
        )

    over_op = assert_problem(await grant(10_001), 422, "progression.grant_limit_exceeded")
    assert over_op["details"]["scope"] == "operation"
    assert (await grant(10_000)).status_code == 201
    assert (await grant(10_000)).status_code == 201
    over_day = assert_problem(await grant(1), 422, "progression.grant_limit_exceeded")
    assert over_day["details"]["scope"] == "day"
    assert_problem(await grant(5, "коротко"), 422, "progression.reason_too_short")
    assert await progression.balance(user) == 3 + 20_000

    # на следующие сутки (Europe/Moscow) лимит снова свободен
    progression.clock.advance(timedelta(days=1))
    assert (await grant(1)).status_code == 201

    ghost = await call(
        progression,
        "POST",
        f"/admin/v1/users/{uuid7()}/ledger/grant",
        {"amount": 5, **REASON},
        perm=GRANT,
    )
    assert_problem(ghost, 404, "progression.user_not_found")


async def test_grant_without_published_config_is_unavailable(progression: Service) -> None:
    response = await call(
        progression,
        "POST",
        f"/admin/v1/users/{uuid7()}/ledger/grant",
        {"amount": 5, **REASON},
        perm=GRANT,
    )
    assert_problem(response, 503, "progression.config_unavailable")


async def test_parallel_grants_cannot_bypass_day_limit(progression: Service) -> None:
    await progression.publish_config(admin__grant_max_per_user_day=100)
    user = await player(progression)
    path = f"/admin/v1/users/{user}/ledger/grant"
    responses = await asyncio.gather(
        *[call(progression, "POST", path, {"amount": 60, **REASON}, perm=GRANT) for _ in range(6)]
    )
    codes = sorted(r.status_code for r in responses)
    assert codes == [201, 422, 422, 422, 422, 422], [r.text for r in responses]
    assert await progression.balance(user) == 3 + 60


# ------------------------------------------------------------------ сторно
async def grant_entry(progression: Service, user: UUID, amount: int = 100) -> str:
    response = await call(
        progression,
        "POST",
        f"/admin/v1/users/{user}/ledger/grant",
        {"amount": amount, **REASON},
        perm=GRANT,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["entry"]["op_id"])


async def test_reverse_posts_counter_entry_once(progression: Service) -> None:
    await progression.publish_config()
    user = await player(progression)
    op_id = await grant_entry(progression, user)
    path = f"/admin/v1/ledger/{op_id}/reverse"
    idem = key()
    reversed_ = await call(progression, "POST", path, REASON, perm=REVERSE, headers=idem)
    assert reversed_.status_code == 201, reversed_.text
    entry = reversed_.json()["entry"]
    assert (entry["direction"], entry["amount"], entry["reversal_of"]) == ("debit", 100, op_id)
    assert entry["reason"] == "reversal"
    assert entry["source_ref"].startswith("admin_action:")  # причина - только в аудите
    assert reversed_.json()["balance_after"] == 3
    replay = await call(progression, "POST", path, REASON, perm=REVERSE, headers=idem)
    assert replay.json() == reversed_.json()

    debited = await progression.outbox("progression.CurrencyDebited")
    assert len(debited) == 1
    assert debited[0].payload["reversal_of"] == op_id
    assert_event_matches_schema(debited[0])

    second = await call(progression, "POST", path, REASON, perm=REVERSE)
    assert_problem(second, 409, "progression.already_reversed")
    counter = await call(
        progression, "POST", f"/admin/v1/ledger/{entry['op_id']}/reverse", REASON, perm=REVERSE
    )
    assert_problem(counter, 409, "progression.not_reversible")
    assert await progression.balance(user) == 3
    assert_problem(
        await call(progression, "POST", "/admin/v1/ledger/none/reverse", REASON, perm=REVERSE),
        404,
        "progression.ledger_entry_not_found",
    )


async def test_reverse_below_zero_is_rejected_and_changes_nothing(progression: Service) -> None:
    await progression.publish_config()
    user = await player(progression)
    op_id = await grant_entry(progression, user)
    assert progression.infra.sessions is not None

    async with progression.infra.sessions() as session, session.begin():
        await session.execute(update(balances).where(balances.c.user_id == user).values(amount=10))
    before = await progression.verse_rows(user)
    response = await call(
        progression, "POST", f"/admin/v1/ledger/{op_id}/reverse", REASON, perm=REVERSE
    )
    assert_problem(response, 409, "progression.insufficient_balance")
    assert await progression.balance(user) == 10
    assert await progression.verse_rows(user) == before
    assert [r.action for r in await rows(progression, admin_actions)].count("ledger.reverse") == 0
    rows_after = await rows(progression, currency_ledger)
    assert all(r.reversal_of is None for r in rows_after)


async def test_reverse_needs_long_enough_reason(progression: Service) -> None:
    await progression.publish_config()
    user = await player(progression)
    op_id = await grant_entry(progression, user)
    response = await call(
        progression,
        "POST",
        f"/admin/v1/ledger/{op_id}/reverse",
        {"reason": "ошибка"},
        perm=REVERSE,
    )
    assert_problem(response, 422, "progression.reason_too_short")


# ------------------------------------------------------------------ журнал, задания, бейджи
async def test_ledger_page_uses_cursor_and_shows_balances(progression: Service) -> None:
    await progression.publish_config()
    user = await player(progression)
    for _ in range(3):
        progression.clock.advance(timedelta(minutes=1))
        await grant_entry(progression, user, 10)
    path = f"/admin/v1/users/{user}/ledger"
    first = (await call(progression, "GET", f"{path}?limit=2", perm=READ)).json()
    assert (first["earned"], first["paid"]) == (33, 0)
    assert len(first["items"]) == 2
    assert first["next_cursor"] is not None
    second = (
        await call(progression, "GET", f"{path}?limit=2&cursor={first['next_cursor']}", perm=READ)
    ).json()
    assert len(second["items"]) == 2
    assert second["next_cursor"] is None
    seen = [e["op_id"] for e in first["items"] + second["items"]]
    assert len(set(seen)) == 4
    assert_problem(
        await call(progression, "GET", f"{path}?cursor=!!!", perm=READ),
        422,
        "request.validation_failed",
    )
    assert_problem(
        await call(progression, "GET", f"/admin/v1/users/{uuid7()}/ledger", perm=READ),
        404,
        "progression.user_not_found",
    )


async def test_quests_and_badges_come_from_published_config(progression: Service) -> None:
    await progression.publish_config(rewards=True)
    quests = (await call(progression, "GET", "/admin/v1/quests")).json()
    assert quests["config_version"] == 1
    assert len(quests["items"]) == 6
    first = quests["items"][0]
    assert (first["key"], first["period"], first["completed_total"]) == (
        "play_3_solo",
        "daily",
        0,
    )
    assert first["reward"] == {"xp": 30, "verses": 5}
    badges = (await call(progression, "GET", "/admin/v1/badges")).json()
    assert len(badges["items"]) == 6
    assert badges["items"][0]["unlocked_total"] == 0


async def test_quest_and_badge_counters_count_real_completions(progression: Service) -> None:
    """Счётчики админки (SqlStats) считают выполненные задания и выданные бейджи (P2)."""
    await progression.publish_config(rewards=True)
    user = uuid7()
    await progression.deliver(progression.game_finished(user, [100, 100, 100]))
    quests = (await call(progression, "GET", "/admin/v1/quests")).json()["items"]
    done = {q["key"]: q["completed_total"] for q in quests}
    assert done["play_3_solo"] == 1
    assert sum(done.values()) >= 1


async def test_config_versions_table_keeps_single_draft(
    progression: Service, make_config: Callable[..., dict[str, Any]]
) -> None:
    await progression.publish_config()
    await call(progression, "PUT", "/admin/v1/configs/draft", {"config": make_config(), **REASON})
    await call(progression, "PUT", "/admin/v1/configs/draft", {"config": make_config(), **REASON})
    drafts = [r for r in await rows(progression, config_versions) if r.status == "draft"]
    assert len(drafts) == 1
