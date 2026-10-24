"""Версии конфига через admin-API: черновик, публикация по хешу, откат, аудит, сброс кэша."""

from __future__ import annotations

from typing import Any

import pytest
from social_service.infrastructure.db.tables_moderation import moderation_audit

from gv_core.ids import uuid7
from gv_testkit import assert_problem

from .conftest import config_body

pytestmark = pytest.mark.component

Social = Any  # фикстура `social` описана в conftest.py
MANAGE = ("admin.access", "social.config.manage")
PUBLISH = ("admin.access", "social.config.publish")
REASON = "меняем лимит друзей"


def _auth(social: Social, perms: tuple[str, ...], user: Any = None) -> dict[str, str]:
    token = social.issuer.issue(user_id=user or uuid7(), perms=perms)
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"idem-{uuid7().hex}"}


async def _save(social: Social, body: dict[str, Any], user: Any = None) -> Any:
    return await social.client.put(
        "/admin/v1/config/draft",
        json={"config": body, "reason": REASON},
        headers=_auth(social, MANAGE, user),
    )


async def test_save_publish_changes_limit_without_restart(social: Social) -> None:
    await social.publish_config(1)
    before = await social.container.config.current()  # кэш прогрет
    assert before.friends.max_friends == 200

    saved = await _save(social, config_body(friends__max_friends=5))
    assert saved.status_code == 200, saved.text
    assert saved.headers["cache-control"] == "no-store"
    card = saved.json()["card"]
    assert card["status"] == "draft" and card["version"] == 2
    assert (await social.container.config.current()).friends.max_friends == 200  # ещё черновик

    published = await social.client.post(
        "/admin/v1/config/draft/publish",
        json={"version": 2, "body_sha256": card["body_sha256"], "reason": REASON},
        headers=_auth(social, PUBLISH),
    )
    assert published.status_code == 200, published.text
    assert published.json()["card"]["status"] == "published"
    # Часы не двигались: кэш процесса сброшен самой публикацией.
    assert (await social.container.config.current()).friends.max_friends == 5

    listing = await social.client.get("/admin/v1/config/versions", headers=_auth(social, MANAGE))
    assert [(i["version"], i["status"]) for i in listing.json()["items"]] == [
        (2, "published"),
        (1, "archived"),
    ]
    actions = sorted(r.action for r in await social.rows(moderation_audit))
    assert actions == ["config.publish", "config.save_draft"]


async def test_invalid_draft_is_422_with_error_paths(social: Social) -> None:
    await social.publish_config(1)
    body = config_body()
    body["friends"]["max_friends"] = -1
    response = await _save(social, body)
    problem = assert_problem(response, 422, "social.config_invalid")
    errors = problem["details"]["errors"]
    assert any("max_friends" in e["path"] for e in errors) and all("reason" in e for e in errors)


async def test_publish_is_bound_to_body_hash_and_draft_state(social: Social) -> None:
    await social.publish_config(1)
    saved = (await _save(social, config_body(friends__max_friends=7))).json()
    publish = "/admin/v1/config/draft/publish"
    wrong = await social.client.post(
        publish,
        json={"version": 2, "body_sha256": "0" * 64, "reason": REASON},
        headers=_auth(social, PUBLISH),
    )
    assert wrong.status_code == 409 and wrong.json()["code"] == "social.config_draft_changed"
    not_draft = await social.client.post(
        publish,
        json={"version": 1, "body_sha256": saved["card"]["body_sha256"], "reason": REASON},
        headers=_auth(social, PUBLISH),
    )
    assert_problem(not_draft, 409, "social.config_not_draft")
    missing = await social.client.post(
        publish,
        json={"version": 99, "body_sha256": "0" * 64, "reason": REASON},
        headers=_auth(social, PUBLISH),
    )
    assert_problem(missing, 404, "social.config_version_not_found")


async def test_manage_cannot_publish_and_replay_is_idempotent(social: Social) -> None:
    await social.publish_config(1)
    headers = _auth(social, MANAGE)
    body = {"config": config_body(friends__max_friends=9), "reason": REASON}
    first = await social.client.put("/admin/v1/config/draft", json=body, headers=headers)
    second = await social.client.put("/admin/v1/config/draft", json=body, headers=headers)
    assert first.status_code == second.status_code == 200 and first.json() == second.json()
    forbidden = await social.client.post(
        "/admin/v1/config/draft/publish",
        json={"version": 2, "body_sha256": first.json()["card"]["body_sha256"], "reason": REASON},
        headers=_auth(social, MANAGE),
    )
    assert_problem(forbidden, 403, "auth.forbidden")


async def test_moderator_perms_cannot_publish_or_rollback(social: Social) -> None:
    """Права moderator (identity 0018): очередь, чтение и черновик - без публикации и отката."""
    await social.publish_config(1)
    moderator = ("admin.access", "social.moderate", "social.config.manage")
    saved = await social.client.put(
        "/admin/v1/config/draft",
        json={"config": config_body(friends__max_friends=9), "reason": REASON},
        headers=_auth(social, moderator),
    )
    assert saved.status_code == 200, saved.text
    publish = await social.client.post(
        "/admin/v1/config/draft/publish",
        json={"version": 2, "body_sha256": saved.json()["card"]["body_sha256"], "reason": REASON},
        headers=_auth(social, moderator),
    )
    assert_problem(publish, 403, "auth.forbidden")
    rollback = await social.client.post(
        "/admin/v1/config/versions/1/rollback",
        json={"reason": "откат"},
        headers=_auth(social, moderator),
    )
    assert_problem(rollback, 403, "auth.forbidden")
    assert (await social.container.config.current()).friends.max_friends == 200


async def test_rollback_copies_published_version_into_draft(social: Social) -> None:
    await social.publish_config(1)
    await social.publish_config(2, friends__max_friends=5)
    rollback = await social.client.post(
        "/admin/v1/config/versions/1/rollback",
        json={"reason": "откат"},
        headers=_auth(social, PUBLISH),
    )
    assert rollback.status_code == 201, rollback.text
    assert rollback.json()["card"]["status"] == "draft"
    assert rollback.json()["config"]["friends"]["max_friends"] == 200
    # Откат только создаёт черновик: действует по-прежнему версия 2.
    assert (await social.container.config.current()).friends.max_friends == 5
    draft = rollback.json()["card"]["version"]
    again = await social.client.post(
        f"/admin/v1/config/versions/{draft}/rollback",
        json={"reason": "откат"},
        headers=_auth(social, PUBLISH),
    )
    assert_problem(again, 409, "social.config_never_published")
    nothing = await social.client.get("/admin/v1/config/versions/99", headers=_auth(social, MANAGE))
    assert_problem(nothing, 404, "social.config_version_not_found")
