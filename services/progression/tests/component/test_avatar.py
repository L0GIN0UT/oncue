"""Своя аватарка (5b, S3; ADR 0048): право, заявка, решения модератора по версиям, оформление."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import pytest
from progression_service.application.ports.avatar import UgcUpload
from progression_service.domain.avatar import (
    AvatarInvalidImageError,
    AvatarTooLargeError,
    AvatarUnavailableError,
)
from progression_service.settings import ProgressionSettings

from gv_core.ids import uuid7
from gv_messaging import EventEnvelope, Outcome
from gv_testkit import FakeClock, assert_problem, load_example

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PRIVATE = "private, no-store"
DEFAULTS = ProgressionSettings()


@dataclass
class FakeMedia:
    """media-pipeline: один ассет на пару (владелец, Idempotency-Key), как в настоящем."""

    clock: FakeClock
    calls: list[dict[str, Any]] = field(default_factory=list)
    uploads: dict[tuple[UUID, str], UgcUpload] = field(default_factory=dict)
    error: Exception | None = None

    async def submit(
        self, *, owner_user_id: UUID, purpose: str, data: bytes, idempotency_key: str
    ) -> UgcUpload:
        self.calls.append(
            {"owner": owner_user_id, "purpose": purpose, "size": len(data), "key": idempotency_key}
        )
        if self.error is not None:
            raise self.error
        return self.uploads.setdefault(
            (owner_user_id, idempotency_key), UgcUpload(uuid7(), self.clock.now())
        )


@pytest.fixture
def media(progression: Service) -> FakeMedia:
    fake = FakeMedia(progression.clock)
    progression.container.media_ugc = fake
    return fake


def moderated(
    asset: UUID,
    owner: UUID,
    status: str,
    version: int,
    *,
    key: str | None = None,
    reason: str | None = None,
    purpose: str = "avatar",
    at: datetime | None = None,
    **patch: Any,
) -> EventEnvelope:
    """``media_pipeline.AssetModerated`` (новый ``event_id`` на каждый вызов)."""
    base = load_example("media_pipeline.AssetModerated")
    payload = {
        **base.payload,
        "asset_id": str(asset),
        "owner_user_id": str(owner),
        "purpose": purpose,
        "status": status,
        "reason_code": reason if status == "rejected" else None,
        "public_key": (key or f"u/{secrets.token_hex(16)}.webp") if status == "approved" else None,
        "decided_at": (at or base.occurred_at).isoformat(),
        "version": version,
        **patch,
    }
    return base.model_copy(update={"aggregate_id": str(asset), "payload": payload})


async def buyer(svc: Service, *, again: bool = False) -> UUID:
    """Игрок с купленным товаром «своя аватарка» (ключ из примера конфига).

    ``again=True`` - ещё один покупатель того же товара (конфиг и предмет уже есть).
    """
    if again:
        [row] = await svc.rows("items", key="custom_avatar")
        item = row.id
    else:
        await svc.publish_config()
        item = await svc.add_item("custom_avatar", slot="avatar", price=10)
    user = uuid7()
    await svc.grant_verses(user, 10)
    assert (await svc.purchase(user, item)).status_code == 200
    return user


async def upload(svc: Service, user: UUID, key: str | None = None, data: bytes = PNG) -> Any:
    headers = {**svc.user(user), "Idempotency-Key": key or f"idem-{uuid7().hex}"}
    return await svc.client.post(
        "/v1/me/avatar", headers=headers, files={"file": ("a.png", data, "image/png")}
    )


async def avatar(svc: Service, user: UUID) -> dict[str, Any]:
    response = await svc.get("/v1/me/avatar", user)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    body: dict[str, Any] = response.json()
    return body


# ------------------------------------------------------------------ публикация конфига
async def publish_draft(svc: Service, config: dict[str, Any]) -> Any:
    """Черновик ``config`` -> публикация админом; возвращает ответ публикации."""
    token = svc.issuer.issue(
        user_id=UUID(int=77),
        perms=("admin.access", "progression.config.manage", "progression.config.publish"),
    )

    def headers() -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"idem-{uuid7().hex}"}

    reason = {"reason": "проверка товара аватарки"}
    saved = await svc.client.put(
        "/admin/v1/configs/draft", json={"config": config, **reason}, headers=headers()
    )
    assert saved.status_code == 200, saved.text
    card = saved.json()["card"]
    return await svc.client.post(
        "/admin/v1/configs/draft/publish",
        json={"version": card["version"], "body_sha256": card["body_sha256"], **reason},
        headers=headers(),
    )


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        (None, "нет предмета"),
        ({"slot": "avatar", "price": None, "level_required": 5}, "без цены"),
        ({"slot": "ghost", "price": 10}, "не в shop.slots"),
    ],
)
async def test_publish_rejects_config_whose_avatar_item_cannot_be_bought(
    progression: Service, make_config: Any, item: dict[str, Any] | None, reason: str
) -> None:
    await progression.publish_config()
    if item is not None:
        await progression.add_item("custom_avatar", **item)

    response = await publish_draft(progression, make_config())

    assert_problem(response, 422, "progression.config_invalid")
    [error] = response.json()["details"]["errors"]
    assert error["path"] == "shop.custom_avatar_item_key" and reason in error["reason"]
    assert [v.status for v in await progression.rows("config_versions", version=1)] == [
        "published"
    ]  # действующая версия не тронута


async def test_publish_accepts_sellable_avatar_item_or_no_avatar_item(
    progression: Service, make_config: Any
) -> None:
    await progression.publish_config()
    await progression.add_item("custom_avatar", slot="avatar", price=10)
    assert (await publish_draft(progression, make_config())).status_code == 200
    without = make_config(shop__custom_avatar_item_key=None)
    assert (await publish_draft(progression, without)).status_code == 200


# ------------------------------------------------------------------ право и заявка
async def test_state_before_purchase_and_limits(progression: Service) -> None:
    await progression.publish_config()
    body = await avatar(progression, uuid7())
    assert body["entitled"] is False and body["current_url"] is None and body["request"] is None
    assert body["limits"] == {
        "max_bytes": DEFAULTS.avatar_max_bytes,
        "formats": list(DEFAULTS.avatar_formats),
        "min_side_px": DEFAULTS.avatar_min_side_px,
        "max_side_px": DEFAULTS.avatar_max_side_px,
    }


async def test_upload_without_purchase_is_403_and_file_is_not_sent(
    progression: Service, media: FakeMedia
) -> None:
    await progression.publish_config()
    response = await upload(progression, uuid7())
    assert_problem(response, 403, "progression.avatar_not_entitled")
    assert media.calls == []


async def test_upload_without_shop_config_is_403(progression: Service, media: FakeMedia) -> None:
    await progression.publish_config(shop__custom_avatar_item_key=None)
    assert_problem(await upload(progression, uuid7()), 403, "progression.avatar_not_entitled")
    assert media.calls == []


async def test_upload_creates_pending_request_and_keeps_previous_avatar(
    progression: Service, media: FakeMedia
) -> None:
    user = await buyer(progression)
    assert (await avatar(progression, user))["entitled"] is True

    response = await upload(progression, user, "idem-avatar-0001")

    assert response.status_code == 202, response.text
    assert response.headers["cache-control"] == PRIVATE
    body = response.json()
    assert body["status"] == "pending" and body["reason_code"] is None
    assert media.calls == [
        {"owner": user, "purpose": "avatar", "size": len(PNG), "key": "idem-avatar-0001"}
    ]
    state = await avatar(progression, user)
    assert state["current_url"] is None  # до решения модератора виден прежний аватар
    assert state["request"]["asset_id"] == body["asset_id"]


async def test_upload_replay_returns_the_same_request(
    progression: Service, media: FakeMedia
) -> None:
    user = await buyer(progression)
    first = await upload(progression, user, "idem-avatar-0001")
    again = await upload(progression, user, "idem-avatar-0001")
    assert again.status_code == 202 and again.json() == first.json()
    assert len(await progression.rows("avatar_requests", user)) == 1
    assert [c["key"] for c in media.calls] == ["idem-avatar-0001"] * 2  # ключ пробрасывается


async def test_new_request_supersedes_pending_one(progression: Service, media: FakeMedia) -> None:
    user = await buyer(progression)
    first = (await upload(progression, user)).json()
    second = (await upload(progression, user)).json()
    by_asset = {str(r.asset_id): r for r in await progression.rows("avatar_requests", user)}
    assert by_asset[first["asset_id"]].status == "rejected"
    assert by_asset[first["asset_id"]].reason_code == "superseded"
    assert by_asset[second["asset_id"]].status == "pending"
    assert (await avatar(progression, user))["request"]["asset_id"] == second["asset_id"]


async def test_concurrent_requests_of_one_user_leave_exactly_one_pending(
    progression: Service, media: FakeMedia
) -> None:
    """Инвариант схемы: не больше одной pending-заявки на игрока (даже при гонке)."""
    import asyncio

    user = await buyer(progression)
    responses = await asyncio.gather(*(upload(progression, user) for _ in range(4)))
    assert [r.status_code for r in responses] == [202] * 4
    rows = await progression.rows("avatar_requests", user)
    assert sorted(r.status for r in rows) == ["pending", "rejected", "rejected", "rejected"]
    assert {r.reason_code for r in rows if r.status == "rejected"} == {"superseded"}


async def test_schema_forbids_two_pending_requests_of_one_user(progression: Service) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    user = uuid7()
    sql = text(
        "INSERT INTO avatar_requests (id, user_id, asset_id, status, submitted_at) "
        "VALUES (:id, :u, :a, :s, now())"
    )
    assert progression.infra.sessions is not None
    async with progression.infra.sessions() as session, session.begin():
        await session.execute(sql, {"id": uuid7(), "u": user, "a": uuid7(), "s": "pending"})
        await session.execute(sql, {"id": uuid7(), "u": user, "a": uuid7(), "s": "rejected"})
        await session.execute(sql, {"id": uuid7(), "u": uuid7(), "a": uuid7(), "s": "pending"})
    with pytest.raises(IntegrityError):
        async with progression.infra.sessions() as session, session.begin():
            await session.execute(sql, {"id": uuid7(), "u": user, "a": uuid7(), "s": "pending"})


async def test_submit_rate_limit_per_user_is_429_with_retry_after(
    progression: Service, media: FakeMedia
) -> None:
    """Декодирование картинки дорого: лимит заявок на игрока считается до вызова media."""
    user = await buyer(progression)
    for _ in range(DEFAULTS.avatar_submit_limit):
        assert (await upload(progression, user)).status_code == 202
    over = await upload(progression, user)
    assert_problem(over, 429, "progression.avatar_rate_limited")
    assert 1 <= int(over.headers["Retry-After"]) <= DEFAULTS.avatar_submit_window_s
    assert len(media.calls) == DEFAULTS.avatar_submit_limit  # лишняя заявка до media не дошла
    # лимит персональный
    other = await buyer(progression, again=True)
    assert (await upload(progression, other)).status_code == 202


async def test_submit_rate_limit_is_fail_closed_without_limiter(
    progression: Service, media: FakeMedia
) -> None:
    from progression_service.infrastructure.adapters import UnconfiguredLimiter

    user = await buyer(progression)
    progression.container.limiter = UnconfiguredLimiter(7)
    response = await upload(progression, user)
    assert_problem(response, 503, "progression.limiter_unavailable")
    assert response.headers["Retry-After"] == "7"
    assert media.calls == []


async def test_not_entitled_upload_does_not_spend_the_limit(
    progression: Service, media: FakeMedia
) -> None:
    await progression.publish_config()
    stranger = uuid7()
    for _ in range(DEFAULTS.avatar_submit_limit + 1):
        assert_problem(await upload(progression, stranger), 403, "progression.avatar_not_entitled")


async def test_oversized_file_is_413_before_media(progression: Service, media: FakeMedia) -> None:
    user = await buyer(progression)
    big = b"x" * (DEFAULTS.avatar_max_bytes + 1)
    response = await upload(progression, user, data=big)
    assert_problem(response, 413, "ugc.too_large")
    assert response.json()["details"]["max_bytes"] == DEFAULTS.avatar_max_bytes
    assert media.calls == []


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (AvatarInvalidImageError(), 422, "ugc.invalid_image"),
        (AvatarTooLargeError(1), 413, "ugc.too_large"),
        (AvatarUnavailableError(), 503, "progression.avatar_unavailable"),
    ],
)
async def test_media_errors_are_mapped(
    progression: Service, media: FakeMedia, error: Exception, status: int, code: str
) -> None:
    user = await buyer(progression)
    media.error = error
    assert_problem(await upload(progression, user), status, code)
    assert await progression.rows("avatar_requests", user) == []


# ------------------------------------------------------------------ решения модератора
async def test_approval_sets_current_url_and_public_appearance(
    progression: Service, media: FakeMedia
) -> None:
    user = await buyer(progression)
    asset = UUID((await upload(progression, user)).json()["asset_id"])
    key = f"u/{secrets.token_hex(16)}.webp"

    outcome = await progression.deliver(moderated(asset, user, "approved", 1, key=key))

    assert outcome is Outcome.PROCESSED
    state = await avatar(progression, user)
    assert state["current_url"] == f"/ugc/{key}"
    assert state["request"]["status"] == "approved"
    public = (await progression.get(f"/v1/appearance?user_ids={user}", uuid7())).json()["players"][
        0
    ]
    assert public["custom_avatar_url"] == f"/ugc/{key}"


async def test_appearance_without_custom_avatar_has_null_url(progression: Service) -> None:
    user = await buyer(progression)
    public = (await progression.get(f"/v1/appearance?user_ids={user}", uuid7())).json()
    assert public["players"][0]["custom_avatar_url"] is None


async def test_duplicate_event_has_one_effect(progression: Service, media: FakeMedia) -> None:
    user = await buyer(progression)
    asset = UUID((await upload(progression, user)).json()["asset_id"])
    key = f"u/{secrets.token_hex(16)}.webp"
    event = moderated(asset, user, "approved", 1, key=key)
    assert await progression.deliver(event) is Outcome.PROCESSED
    assert await progression.deliver(event) is Outcome.DUPLICATE  # тот же event_id
    again = moderated(asset, user, "approved", 1, key=f"u/{secrets.token_hex(16)}.webp")
    await progression.deliver(again)  # новый event_id, та же версия
    assert (await avatar(progression, user))["current_url"] == f"/ugc/{key}"
    assert len(await progression.rows("user_avatars", user)) == 1


async def test_out_of_order_takedown_wins_over_late_approval(
    progression: Service, media: FakeMedia
) -> None:
    user = await buyer(progression)
    asset = UUID((await upload(progression, user)).json()["asset_id"])
    await progression.deliver(moderated(asset, user, "rejected", 2, reason="copyright"))
    await progression.deliver(moderated(asset, user, "approved", 1))  # опоздало
    state = await avatar(progression, user)
    assert state["current_url"] is None
    assert state["request"]["status"] == "rejected"
    assert state["request"]["reason_code"] == "copyright"


async def test_rejection_keeps_previous_avatar(progression: Service, media: FakeMedia) -> None:
    user = await buyer(progression)
    first = UUID((await upload(progression, user)).json()["asset_id"])
    key = f"u/{secrets.token_hex(16)}.webp"
    await progression.deliver(moderated(first, user, "approved", 1, key=key))
    second = UUID((await upload(progression, user)).json()["asset_id"])

    await progression.deliver(moderated(second, user, "rejected", 1, reason="low_quality"))

    state = await avatar(progression, user)
    assert state["current_url"] == f"/ugc/{key}"  # прежний аватар остался
    assert state["request"]["asset_id"] == str(second)
    assert (state["request"]["status"], state["request"]["reason_code"]) == (
        "rejected",
        "low_quality",
    )


async def test_takedown_of_current_avatar_returns_slot_item(
    progression: Service, media: FakeMedia
) -> None:
    user = await buyer(progression)
    asset = UUID((await upload(progression, user)).json()["asset_id"])
    await progression.deliver(moderated(asset, user, "approved", 1))
    assert (await avatar(progression, user))["current_url"] is not None

    await progression.deliver(moderated(asset, user, "rejected", 2, reason="inappropriate"))

    assert (await avatar(progression, user))["current_url"] is None
    assert await progression.rows("user_avatars", user) == []
    public = (await progression.get(f"/v1/appearance?user_ids={user}", uuid7())).json()
    assert public["players"][0]["custom_avatar_url"] is None


async def test_newer_approval_replaces_older_and_older_one_cannot_come_back(
    progression: Service, media: FakeMedia
) -> None:
    user = await buyer(progression)
    old_asset, new_asset = uuid7(), uuid7()
    old_key, new_key = (f"u/{secrets.token_hex(16)}.webp" for _ in range(2))
    t0 = progression.clock.now()
    t1 = t0.replace(minute=t0.minute + 5)
    await progression.deliver(moderated(new_asset, user, "approved", 1, key=new_key, at=t1))
    await progression.deliver(moderated(old_asset, user, "approved", 1, key=old_key, at=t0))
    assert (await avatar(progression, user))["current_url"] == f"/ugc/{new_key}"
    # снятие устаревшего ассета не трогает действующий
    await progression.deliver(moderated(old_asset, user, "rejected", 2, reason="other"))
    assert (await avatar(progression, user))["current_url"] == f"/ugc/{new_key}"


async def test_event_of_other_purpose_is_ignored(progression: Service, media: FakeMedia) -> None:
    user = await buyer(progression)
    outcome = await progression.deliver(
        moderated(uuid7(), user, "approved", 1, purpose="collection_cover")
    )
    assert outcome is Outcome.PROCESSED
    assert await progression.rows("avatar_requests") == []
    assert await progression.rows("user_avatars") == []


@pytest.mark.parametrize(
    "patch",
    [
        {"version": 0},
        {"status": "weird"},
        {"asset_id": "not-a-uuid"},
        {"public_key": "u/../../etc.webp"},
        {"public_key": None},  # approved без ключа
    ],
)
async def test_invalid_payload_goes_to_dead_letters(
    progression: Service, media: FakeMedia, patch: dict[str, Any]
) -> None:
    user = await buyer(progression)
    valid = moderated(uuid7(), user, "approved", 1)
    event = valid.model_copy(update={"payload": {**valid.payload, **patch}})
    assert await progression.deliver(event) is Outcome.DEAD
    assert progression.dead_letters.letters[-1]["reason"] == "permanent"
    assert await progression.rows("user_avatars") == []
