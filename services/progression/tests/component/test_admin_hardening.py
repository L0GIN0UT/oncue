"""Admin-API progression: привязка публикации к хешу, ограничения сторно, аудит, метрика, сутки."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
import structlog
from progression_service.infrastructure.db.tables import currency_ledger, xp_ledger
from progression_service.infrastructure.db.tables_admin import admin_actions, idempotency_keys
from prometheus_client import REGISTRY
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from gv_core.ids import uuid7
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py
REASON_TEXT = "секретная причина выдачи"
REASON = {"reason": REASON_TEXT}
ADMIN_A = UUID(int=101)
ADMIN_B = UUID(int=102)
GRANT = "progression.ledger.grant"
REVERSE = "progression.ledger.reverse"
MANAGE = "progression.config.manage"
PUBLISH = "progression.config.publish"
METRIC = "gv_progression_admin_operations_total"


def key() -> dict[str, str]:
    return {"Idempotency-Key": f"idem-{uuid7().hex}"}


async def call(
    svc: Service,
    method: str,
    path: str,
    body: Any = None,
    *,
    perm: str,
    actor: UUID = ADMIN_A,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    token = svc.issuer.issue(user_id=actor, perms=("admin.access", perm))
    sent = {"Authorization": f"Bearer {token}", **(headers if headers is not None else key())}
    return await svc.client.request(method, path, json=body, headers=sent)


async def player(svc: Service) -> UUID:
    user = uuid7()
    await svc.deliver(svc.game_finished(user, [100, 100, 100]))
    return user


async def rows(svc: Service, table: Any) -> list[Any]:
    assert svc.infra.sessions is not None
    async with svc.infra.sessions() as session:
        return list((await session.execute(select(table))).all())


def metric(operation: str, outcome: str) -> float:
    return REGISTRY.get_sample_value(METRIC, {"operation": operation, "outcome": outcome}) or 0.0


async def grant(
    svc: Service, user: UUID, amount: int, *, headers: dict[str, str] | None = None, **extra: Any
) -> httpx.Response:
    return await call(
        svc,
        "POST",
        f"/admin/v1/users/{user}/ledger/grant",
        {"amount": amount, **REASON, **extra},
        perm=GRANT,
        headers=headers,
    )


async def reverse(
    svc: Service, op_id: str, *, actor: UUID = ADMIN_A, headers: dict[str, str] | None = None
) -> httpx.Response:
    return await call(
        svc, "POST", f"/admin/v1/ledger/{op_id}/reverse", REASON, perm=REVERSE,
        actor=actor, headers=headers,
    )  # fmt: skip


# ------------------------------------------------------------------ публикация по хешу
async def test_publish_is_bound_to_the_draft_body_that_was_seen(
    progression: Service, make_config: Callable[..., dict[str, Any]]
) -> None:
    await progression.publish_config()
    path = "/admin/v1/configs/draft"
    a = await call(
        progression, "PUT", path, {"config": make_config(levels__base=600), **REASON}, perm=MANAGE
    )
    b = await call(
        progression,
        "PUT",
        path,
        {"config": make_config(levels__base=700), **REASON},
        perm=MANAGE,
        actor=ADMIN_B,
    )
    hash_a, hash_b = a.json()["card"]["body_sha256"], b.json()["card"]["body_sha256"]
    assert a.json()["card"]["version"] == b.json()["card"]["version"] == 2
    assert hash_a != hash_b

    stale = await call(
        progression, "POST", f"{path}/publish",
        {"version": 2, "body_sha256": hash_a, **REASON}, perm=PUBLISH,
    )  # fmt: skip
    assert_problem(stale, 409, "progression.draft_changed")
    assert (await progression.scalar(select(text("count(*)")).select_from(admin_actions))) == 2
    fresh = await call(
        progression, "POST", f"{path}/publish",
        {"version": 2, "body_sha256": hash_b, **REASON}, perm=PUBLISH,
    )  # fmt: skip
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["config"]["levels"]["base"] == 700


async def test_resaving_draft_refreshes_created_at_and_rollback_audits_replaced_draft(
    progression: Service, make_config: Callable[..., dict[str, Any]]
) -> None:
    await progression.publish_config()
    path = "/admin/v1/configs/draft"
    first = await call(progression, "PUT", path, {"config": make_config(), **REASON}, perm=MANAGE)
    progression.clock.advance(timedelta(minutes=5))
    second = await call(
        progression, "PUT", path, {"config": make_config(levels__base=800), **REASON}, perm=MANAGE
    )
    assert second.json()["card"]["created_at"] > first.json()["card"]["created_at"]

    rolled = await call(progression, "POST", "/admin/v1/configs/1/rollback", REASON, perm=PUBLISH)
    assert rolled.status_code == 201, rolled.text
    audit = [r for r in await rows(progression, admin_actions) if r.action == "config.rollback"]
    assert audit[0].details["replaced_draft_version"] == 2


# ------------------------------------------------------------------ сторно: ограничения
async def test_only_credits_of_the_currency_ledger_are_reversible(progression: Service) -> None:
    await progression.publish_config()
    user = await player(progression)
    assert progression.infra.sessions is not None
    async with progression.infra.sessions() as session, session.begin():
        await session.execute(
            currency_ledger.insert().values(
                id=uuid7(), op_id="shop:1", user_id=user, direction="debit", amount=1,
                reason="purchase", source_ref="item", reversal_of=None, actor_id=None,
                config_version=1, at=progression.clock.now(), bucket="earned",
            )
        )  # fmt: skip
    assert_problem(await reverse(progression, "shop:1"), 409, "progression.not_reversible")

    xp_op = (await rows(progression, xp_ledger))[0].op_id
    assert_problem(await reverse(progression, xp_op), 409, "progression.not_reversible")
    assert_problem(await reverse(progression, "nope"), 404, "progression.ledger_entry_not_found")


async def test_paid_bucket_is_rejected_for_manual_grant(progression: Service) -> None:
    await progression.publish_config()
    user = await player(progression)
    response = await grant(progression, user, 5, bucket="paid")
    assert_problem(response, 422, "request.validation_failed")
    assert (await grant(progression, user, 5, bucket="earned")).status_code == 201


# ------------------------------------------------------------------ причина только в аудите
async def test_reason_lives_only_in_audit(progression: Service) -> None:
    await progression.publish_config()
    user = await player(progression)
    granted = await grant(progression, user, 10)
    op = granted.json()["entry"]["op_id"]
    reversed_ = await reverse(progression, op)
    assert reversed_.status_code == 201, reversed_.text

    audit = {r.action: r for r in await rows(progression, admin_actions)}
    ledger = {r.op_id: r for r in await rows(progression, currency_ledger)}
    grant_row, reverse_row = ledger[op], ledger[reversed_.json()["entry"]["op_id"]]
    assert grant_row.source_ref == f"admin_action:{audit['ledger.grant'].id}"
    assert reverse_row.source_ref == f"admin_action:{audit['ledger.reverse'].id}"
    assert reverse_row.reversal_of == op
    assert audit["ledger.grant"].reason == REASON_TEXT
    for event in await progression.outbox():
        assert REASON_TEXT not in str(event.payload)
    assert REASON_TEXT not in granted.text
    assert audit["ledger.reverse"].target == f"user:{user}"


# ------------------------------------------------------------------ лимит суток
async def test_day_limit_counts_reversed_grants(progression: Service) -> None:
    await progression.publish_config(admin__grant_max_per_user_day=100)
    user = await player(progression)
    first = await grant(progression, user, 100)
    assert first.status_code == 201
    assert (await reverse(progression, first.json()["entry"]["op_id"])).status_code == 201
    again = await grant(progression, user, 1)
    problem = assert_problem(again, 422, "progression.grant_limit_exceeded")
    assert problem["details"]["scope"] == "day"
    assert problem["details"]["granted_today"] == 100


async def test_day_boundary_uses_config_timezone(progression: Service) -> None:
    # Asia/Kolkata: локальная полночь = 18:30 UTC; START = 12:00 UTC.
    await progression.publish_config(timezone="Asia/Kolkata", admin__grant_max_per_user_day=100)
    user = await player(progression)
    progression.clock.advance(timedelta(hours=6, minutes=25))  # 18:25 UTC = 23:55 местного
    assert (await grant(progression, user, 100)).status_code == 201
    assert_problem(await grant(progression, user, 1), 422, "progression.grant_limit_exceeded")
    progression.clock.advance(timedelta(minutes=10))  # 18:35 UTC = 00:05 следующих суток
    assert (await grant(progression, user, 100)).status_code == 201


# ------------------------------------------------------------------ идемпотентность
async def test_same_key_other_actor_or_other_body_is_rejected(progression: Service) -> None:
    await progression.publish_config()
    user = await player(progression)
    idem = key()
    ok = await grant(progression, user, 10, headers=idem)
    assert ok.status_code == 201
    other_body = await grant(progression, user, 11, headers=idem)
    assert_problem(other_body, 409, "idempotency.key_reused")
    # другой актор с тем же ключом: op_id `admin:{key}` уже занят - вторая проводка невозможна
    other_actor = await call(
        progression, "POST", f"/admin/v1/users/{user}/ledger/grant",
        {"amount": 10, **REASON}, perm=GRANT, actor=ADMIN_B, headers=idem,
    )  # fmt: skip
    assert_problem(other_actor, 409, "idempotency.key_reused")
    assert await progression.balance(user) == 3 + 10

    op = ok.json()["entry"]["op_id"]
    rev_key = key()
    assert (await reverse(progression, op, headers=rev_key)).status_code == 201
    # тот же ключ у другого актора на другой проводке: op_id сторно занят - второго сторно нет
    second = (await grant(progression, user, 7)).json()["entry"]["op_id"]
    assert_problem(
        await reverse(progression, second, actor=ADMIN_B, headers=rev_key),
        409,
        "idempotency.key_reused",
    )
    assert await progression.balance(user) == 3 + 7


async def test_parallel_repeat_of_the_same_key_is_a_single_posting(progression: Service) -> None:
    await progression.publish_config()
    user = await player(progression)
    idem = key()
    # Поведение зафиксировано: параллельный повтор не возвращает сохранённый ответ, а получает
    # 409 `idempotency.key_reused` (op_id занят проводкой первого запроса); проводка одна.
    results = await asyncio.gather(grant(progression, user, 10, headers=idem),
                                   grant(progression, user, 10, headers=idem))  # fmt: skip
    assert sorted(r.status_code for r in results) == [201, 409]
    loser = next(r for r in results if r.status_code == 409)
    assert_problem(loser, 409, "idempotency.key_reused")
    assert await progression.balance(user) == 3 + 10


# ------------------------------------------------------------------ аудит append-only, ключи
async def test_admin_actions_are_append_only(progression: Service) -> None:
    await progression.publish_config()
    user = await player(progression)
    await grant(progression, user, 10)
    assert progression.infra.sessions is not None
    for statement in (
        "UPDATE admin_actions SET reason = 'x'",
        "DELETE FROM admin_actions",
        "TRUNCATE admin_actions",
    ):
        async with progression.infra.sessions() as session:
            with pytest.raises(DBAPIError, match="append-only"):
                await session.execute(text(statement))
    assert len(await rows(progression, admin_actions)) == 1


async def test_old_idempotency_keys_are_purged_by_periodic_job(progression: Service) -> None:
    await progression.publish_config()
    user = await player(progression)
    await grant(progression, user, 10)
    assert len(await rows(progression, idempotency_keys)) == 1
    retention_h = progression.infra.settings.idempotency.retention_h  # type: ignore[attr-defined]
    progression.clock.advance(timedelta(hours=retention_h + 1))
    await progression.run_job("idempotency.purge")
    assert await rows(progression, idempotency_keys) == []


# ------------------------------------------------------------------ метрика и логи
async def test_metric_and_logs_have_no_reason(progression: Service) -> None:
    await progression.publish_config(admin__grant_max_per_user_day=100)
    user = await player(progression)
    ok_before = metric("ledger_granted", "ok")
    limit_before = metric("ledger_granted", "limit_exceeded")
    rev_before = metric("ledger_reversed", "ok")
    with structlog.testing.capture_logs() as logs:
        granted = await grant(progression, user, 100)
        await grant(progression, user, 1)  # выше суточного лимита
        await reverse(progression, granted.json()["entry"]["op_id"])
    assert metric("ledger_granted", "ok") == ok_before + 1
    assert metric("ledger_granted", "limit_exceeded") == limit_before + 1
    assert metric("ledger_reversed", "ok") == rev_before + 1
    events = [e["event"] for e in logs]
    for expected in (
        "progression.admin.ledger_granted",
        "progression.admin.grant_limit_exceeded",
        "progression.admin.ledger_reversed",
    ):
        assert expected in events
    assert REASON_TEXT not in str(logs)
