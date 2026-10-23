"""Промокоды (ADR 0048): выпуск партий, погашение, перебор, потолок эмиссии."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from progression_service.application.promo import PromoService
from progression_service.infrastructure.adapters import RedisAttemptLimiter, UnconfiguredLimiter
from redis.asyncio import Redis

from gv_core.errors import DependencyUnavailableError
from gv_core.ids import uuid7
from gv_testkit import assert_event_matches_schema, assert_problem

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py
ADMIN_ID = UUID(int=78)
REASON = {"reason": "проверка контракта"}
BATCH = {
    "label": "Весна",
    "reward": {"kind": "verses", "verses_amount": 50},
    "count": 3,
    "max_uses": 1,
    **REASON,
}


def idem() -> dict[str, str]:
    return {"Idempotency-Key": f"idem-{uuid7().hex}"}


async def admin(
    svc: Service, method: str, path: str, headers: dict[str, str] | None = None, **kwargs: Any
) -> httpx.Response:
    token = svc.issuer.issue(user_id=ADMIN_ID, perms=("admin.access", "progression.promo.manage"))
    sent = {"Authorization": f"Bearer {token}", **(headers if headers is not None else idem())}
    return await svc.client.request(method, path, headers=sent, **kwargs)


async def make_batch(svc: Service, **patch: Any) -> dict[str, Any]:
    response = await admin(svc, "POST", "/admin/v1/promo-codes", json={**BATCH, **patch})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def redeem(svc: Service, user: UUID, code: str) -> httpx.Response:
    return await svc.client.post(
        "/v1/promo/redeem", json={"code": code}, headers={**svc.user(user), **idem()}
    )


# ------------------------------------------------------------------ выпуск партий
async def test_batch_returns_codes_once_and_stores_only_keyed_hashes(progression: Service) -> None:
    await progression.publish_config()

    response = await admin(progression, "POST", "/admin/v1/promo-codes", json=BATCH)

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    codes = response.json()["codes"]
    assert len(codes) == 3 and len(set(codes)) == 3
    for code in codes:
        plain = code.replace("-", "")
        assert len(plain) == 12 and not set(plain) & set("01OIL")
    stored = {r.code_hash for r in await progression.rows("promo_codes")}
    assert len(stored) == 3
    for code in codes:
        plain = code.replace("-", "")
        assert plain not in stored
        assert hashlib.sha256(plain.encode()).hexdigest() not in stored  # не голый sha256
    listed = (await admin(progression, "GET", "/admin/v1/promo-codes")).json()
    assert listed["items"][0]["status"] == "active" and listed["items"][0]["codes_total"] == 3
    assert not any(code in json.dumps(listed) for code in codes)


async def test_repeating_batch_creation_never_reveals_codes_again(progression: Service) -> None:
    await progression.publish_config()
    headers = idem()

    first = await admin(progression, "POST", "/admin/v1/promo-codes", headers, json=BATCH)
    again = await admin(progression, "POST", "/admin/v1/promo-codes", headers, json=BATCH)

    assert first.status_code == 201
    body = assert_problem(again, 409, "progression.codes_already_shown")
    assert not any(code in json.dumps(body) for code in first.json()["codes"])
    assert len(await progression.rows("promo_batches")) == 1
    assert len(await progression.rows("promo_codes")) == 3


async def test_emission_ceiling_is_enforced(progression: Service) -> None:
    await progression.publish_config(promo__max_batch_verses=1000)

    over = await admin(
        progression,
        "POST",
        "/admin/v1/promo-codes",
        json={
            **BATCH,
            "count": 21,
            "max_uses": 1,
            "reward": {"kind": "verses", "verses_amount": 50},
        },
    )
    exact = await admin(
        progression,
        "POST",
        "/admin/v1/promo-codes",
        json={
            **BATCH,
            "count": 20,
            "max_uses": 1,
            "reward": {"kind": "verses", "verses_amount": 50},
        },
    )

    assert_problem(over, 422, "progression.promo_batch_too_large")
    assert exact.status_code == 201
    assert len(await progression.rows("promo_batches")) == 1


@pytest.mark.parametrize(
    ("patch", "status", "code"),
    [
        ({"reward": {"kind": "verses"}}, 422, "progression.promo_reward_invalid"),
        (
            {"reward": {"kind": "verses", "verses_amount": 5, "item_id": str(uuid7())}},
            422,
            "progression.promo_reward_invalid",
        ),
        ({"reward": {"kind": "item", "item_id": str(uuid7())}}, 422, "progression.item_not_found"),
        ({"expires_at": "2020-01-01T00:00:00Z"}, 422, "progression.promo_expired"),
    ],
)
async def test_batch_validation(
    progression: Service, patch: dict[str, Any], status: int, code: str
) -> None:
    await progression.publish_config()

    response = await admin(progression, "POST", "/admin/v1/promo-codes", json={**BATCH, **patch})

    assert_problem(response, status, code)


async def test_batch_needs_promo_section(progression: Service) -> None:
    await progression.publish_config(promo=...)

    response = await admin(progression, "POST", "/admin/v1/promo-codes", json=BATCH)

    assert_problem(response, 422, "progression.promo_not_configured")


# ------------------------------------------------------------------ погашение
async def test_redeem_verses_credits_once_and_repeat_returns_same_reward(
    progression: Service,
) -> None:
    await progression.publish_config()
    user = uuid7()
    code = (await make_batch(progression))["codes"][0]

    first = await redeem(progression, user, code)
    again = await redeem(progression, user, code.lower())

    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "private, no-store"
    assert first.json() == {"reward_kind": "verses", "amount": 50, "item": None, "repeated": False}
    assert again.json()["repeated"] is True and again.json()["amount"] == 50
    assert await progression.balance(user) == 50
    (entry,) = await progression.rows("currency_ledger", user)
    (code_row,) = await progression.rows("promo_codes", activations=1)
    assert entry.op_id == f"promo:{code_row.id}:{user}"
    assert (entry.bucket, entry.direction, entry.amount) == ("earned", "credit", 50)
    (event,) = await progression.outbox("progression.PromoRedeemed")
    assert_event_matches_schema(event)
    assert code.replace("-", "") not in json.dumps(event.payload)
    assert event.payload["promo_id"] == str(code_row.id)
    assert len(await progression.outbox("progression.CurrencyCredited")) == 1


async def test_code_input_is_normalized(progression: Service) -> None:
    await progression.publish_config()
    code = (await make_batch(progression))["codes"][0]

    response = await redeem(progression, uuid7(), f"  {code.lower().replace('-', ' ')}  ")

    assert response.status_code == 200


async def test_all_unusable_codes_answer_identically(progression: Service) -> None:
    await progression.publish_config()
    expiring = (
        await make_batch(
            progression, expires_at=(progression.clock.now() + timedelta(hours=1)).isoformat()
        )
    )["codes"][0]
    revoked_batch = await make_batch(progression)
    revoked = revoked_batch["codes"][0]
    exhausted = (await make_batch(progression))["codes"][0]
    assert (await redeem(progression, uuid7(), exhausted)).status_code == 200
    revoke = await admin(
        progression,
        "POST",
        f"/admin/v1/promo-codes/{revoked_batch['id']}/revoke",
        json=REASON,
    )
    assert revoke.status_code == 204
    progression.clock.advance(hours=2)

    answers = [
        await redeem(progression, uuid7(), code)
        for code in ("ZZZZ-ZZZZ-ZZZZ", expiring, revoked, exhausted, "###")
    ]

    bodies = [assert_problem(a, 404, "progression.promo_invalid") for a in answers]
    assert len({(b["title"], b.get("detail"), b["type"]) for b in bodies}) == 1


async def test_last_activation_goes_to_one_user_only(progression: Service) -> None:
    await progression.publish_config()
    code = (await make_batch(progression, count=1, max_uses=2))["codes"][0]
    users = [uuid7() for _ in range(3)]

    statuses = [(await redeem(progression, u, code)).status_code for u in users]

    assert statuses == [200, 200, 404]
    assert sum([await progression.balance(u) for u in users]) == 100


async def test_per_user_once_limits_a_user_to_one_code_per_batch(progression: Service) -> None:
    await progression.publish_config()
    once = (await make_batch(progression, count=2, max_uses=3))["codes"]
    many = (await make_batch(progression, count=2, max_uses=3, per_user_once=False))["codes"]
    user = uuid7()

    assert (await redeem(progression, user, once[0])).status_code == 200
    assert_problem(await redeem(progression, user, once[1]), 404, "progression.promo_invalid")
    assert (await redeem(progression, user, many[0])).status_code == 200
    assert (await redeem(progression, user, many[1])).status_code == 200
    assert await progression.balance(user) == 150


async def test_item_reward_goes_to_inventory_and_owned_item_keeps_the_code(
    progression: Service,
) -> None:
    await progression.publish_config()
    item = await progression.add_item("promo_item", price=10)
    code = (await make_batch(progression, reward={"kind": "item", "item_id": str(item)}))["codes"][
        0
    ]
    user = uuid7()

    first = await redeem(progression, user, code)

    assert first.json()["reward_kind"] == "item" and first.json()["item"]["key"] == "promo_item"
    assert first.json()["amount"] is None
    (row,) = await progression.rows("inventory", user)
    assert (row.item_id, row.source) == (item, "promo")
    assert await progression.verse_rows(user) == 0
    event = (await progression.outbox("progression.PromoRedeemed"))[0]
    assert (event.payload["item_key"], event.payload["amount"]) == ("promo_item", None)
    assert_event_matches_schema(event)

    other_code = (await make_batch(progression, reward={"kind": "item", "item_id": str(item)}))[
        "codes"
    ][0]
    clash = await redeem(progression, user, other_code)
    assert_problem(clash, 404, "progression.promo_invalid")  # не оракул владения
    assert [r.activations for r in await progression.rows("promo_codes")].count(1) == 1


async def test_revoke_stops_new_activations_but_keeps_given_rewards(progression: Service) -> None:
    await progression.publish_config()
    batch = await make_batch(progression, count=1, max_uses=5)
    user = uuid7()
    assert (await redeem(progression, user, batch["codes"][0])).status_code == 200

    path = f"/admin/v1/promo-codes/{batch['id']}/revoke"
    assert (await admin(progression, "POST", path, json=REASON)).status_code == 204
    assert_problem(
        await admin(progression, "POST", f"/admin/v1/promo-codes/{uuid7()}/revoke", json=REASON),
        404,
        "progression.promo_not_found",
    )

    assert_problem(
        await redeem(progression, uuid7(), batch["codes"][0]), 404, "progression.promo_invalid"
    )
    assert await progression.balance(user) == 50
    listed = (await admin(progression, "GET", "/admin/v1/promo-codes")).json()
    assert listed["items"][0]["status"] == "revoked" and listed["items"][0]["activations"] == 1


# ------------------------------------------------------------------ перебор
async def test_attempts_per_user_are_limited_with_retry_after(progression: Service) -> None:
    await progression.publish_config(promo__attempts_per_user=3, promo__attempts_per_ip=100)
    user = uuid7()

    statuses = [(await redeem(progression, user, "AAAA-BBBB-CCCC")).status_code for _ in range(3)]
    blocked = await redeem(progression, user, "AAAA-BBBB-CCCC")

    assert statuses == [404, 404, 404]
    assert_problem(blocked, 429, "progression.promo_rate_limited")
    assert 1 <= int(blocked.headers["retry-after"]) <= 3600
    # другой игрок с того же адреса ещё может пробовать
    assert (await redeem(progression, uuid7(), "AAAA-BBBB-CCCC")).status_code == 404


async def test_attempts_per_ip_are_limited_across_users(progression: Service) -> None:
    await progression.publish_config(promo__attempts_per_user=100, promo__attempts_per_ip=3)

    statuses = [
        (await redeem(progression, uuid7(), "AAAA-BBBB-CCCC")).status_code for _ in range(4)
    ]

    assert statuses == [404, 404, 404, 429]


async def test_limited_user_cannot_redeem_even_a_valid_code(progression: Service) -> None:
    await progression.publish_config(promo__attempts_per_user=1, promo__attempts_per_ip=100)
    user = uuid7()
    code = (await make_batch(progression))["codes"][0]
    await redeem(progression, user, "AAAA-BBBB-CCCC")

    assert_problem(await redeem(progression, user, code), 429, "progression.promo_rate_limited")
    assert await progression.balance(user) == 0


async def test_limiter_failure_closes_the_input(progression: Service) -> None:
    await progression.publish_config()
    code = (await make_batch(progression))["codes"][0]
    broken = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    service = PromoService(
        progression.container.uow_factory(),
        progression.clock,
        progression.container.hasher,
        RedisAttemptLimiter(broken, retry_after_s=7),
    )
    user = uuid7()

    with pytest.raises(DependencyUnavailableError) as err:
        await service.redeem(user, code, client_ip="127.0.0.1")

    assert err.value.code == "progression.limiter_unavailable"
    assert err.value.details == {"retry_after": 7}
    assert await progression.balance(user) == 0
    await broken.aclose()


async def test_missing_redis_closes_the_input_with_503(progression: Service) -> None:
    await progression.publish_config()
    service = PromoService(
        progression.container.uow_factory(),
        progression.clock,
        progression.container.hasher,
        UnconfiguredLimiter(5),
    )

    with pytest.raises(DependencyUnavailableError):
        await service.redeem(uuid7(), "AAAA-BBBB-CCCC", client_ip=None)


async def test_without_promo_section_every_code_is_invalid(progression: Service) -> None:
    await progression.publish_config(promo=...)

    assert_problem(
        await redeem(progression, uuid7(), "AAAA-BBBB-CCCC"), 404, "progression.promo_invalid"
    )
