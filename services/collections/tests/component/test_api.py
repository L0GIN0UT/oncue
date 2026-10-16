"""Границы API: субъекты и права, mass assignment, ``Idempotency-Key``, CSRF, ``GET /v1/meta``.

Заменяет заглушки шага 0a (``test_stage3b_*``): те же маршруты теперь проверяются на
поведении, а не на 501.
"""

from __future__ import annotations

from typing import Any

import pytest

from gv_core.ids import uuid7
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

CID = "0192a3b4-c5d6-7e8f-9a0b-000000000c01"
ITEM = "0192a3b4-c5d6-7e8f-9a0b-000000000100"
USER = "0192a3b4-c5d6-7e8f-9a0b-000000000001"
KEY = {"Idempotency-Key": "test-key-0001"}
BODY = {"title": "Кино · кадры", "items": [{"media_item_id": ITEM, "custom": {"emoji": "🚢"}}]}

USER_ROUTES: list[tuple[str, str, dict[str, object] | None]] = [
    ("POST", "/v1/collections", {"domain": "film", "title": "Кино · кадры"}),
    ("GET", "/v1/me/collections?status=draft", None),
    ("GET", f"/v1/collections/{CID}/draft", None),
    ("PUT", f"/v1/collections/{CID}/draft", {"base_revision": 1, "body": BODY}),
    ("POST", f"/v1/collections/{CID}/publish", {"draft_revision": 2}),
    ("DELETE", f"/v1/collections/{CID}", None),
    ("PUT", f"/v1/collections/{CID}/like", None),
    ("DELETE", f"/v1/collections/{CID}/like", None),
    ("PUT", f"/v1/collections/{CID}/favorite", None),
    ("DELETE", f"/v1/collections/{CID}/favorite", None),
    ("GET", "/v1/me/favorites", None),
]
ADMIN_ROUTES: list[tuple[str, str, dict[str, object] | None, str]] = [
    ("GET", "/admin/v1/collections?status=hidden&kind=ugc", None, "collections.view"),
    ("GET", f"/admin/v1/collections/{CID}", None, "collections.view"),
    (
        "POST",
        "/admin/v1/collections",
        {
            "domain": "film",
            "title": "Лучшее 90-х",
            "default_mode_key": "film.collection.frame",
            "items": [{"media_item_id": ITEM}],
            "reason": "кураторский",
        },
        "collections.curate",
    ),
    (
        "PUT",
        f"/admin/v1/collections/{CID}",
        {
            "base_version": 1,
            "title": "Лучшее 90-х",
            "default_mode_key": "film.collection.frame",
            "items": [{"media_item_id": ITEM}],
            "reason": "правка",
        },
        "collections.curate",
    ),
    ("POST", f"/admin/v1/collections/{CID}/hide", {"reason": "спам"}, "collections.unpublish"),
    ("POST", f"/admin/v1/collections/{CID}/unhide", {"reason": "ошибка"}, "collections.unpublish"),
    (
        "PUT",
        f"/admin/v1/collections/{CID}/featured",
        {"featured": True, "rank": 1, "reason": "подборка недели"},
        "collections.curate",
    ),
    (
        "POST",
        f"/admin/v1/authors/{USER}/restriction",
        {"reason": "спам"},
        "collections.restrict_author",
    ),
    (
        "DELETE",
        f"/admin/v1/authors/{USER}/restriction",
        {"reason": "исправился"},
        "collections.restrict_author",
    ),
]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_health(svc: Any) -> None:
    assert (await svc.client.get("/health/live")).status_code == 200
    assert (await svc.client.get("/health/ready")).status_code == 200


async def test_meta_exposes_real_limits_and_dictionaries(service_factory: Any) -> None:
    svc = await service_factory(publish_per_day=3, items_min=6)
    response = await svc.client.get("/v1/meta")
    data = response.json()
    assert data["service"] == "collections" and data["status"] == "ok"
    assert data["limits"] == {
        "title_max": 80,
        "description_max": 500,
        "items_min": 6,
        "items_max": 200,
        "tags_max": 5,
        "emoji_max_graphemes": 12,
        "emoji_min_graphemes": 6,
        "drafts_max": 20,
        "publish_per_day": 3,
    }
    presets = {p["id"]: p for p in data["cover_presets"]}
    assert presets["night"]["emoji"] == "🌙" and len(presets["night"]["gradient"]) == 3
    tags = {t["key"]: t for t in data["tags"]}
    assert tags["horror"] == {
        "key": "horror",
        "title_key": "collections.tags.horror",
        "domains": ["film"],
    }


@pytest.mark.parametrize(("method", "path", "body"), USER_ROUTES)
async def test_user_routes_reject_anonymous_and_guest(
    svc: Any, method: str, path: str, body: dict[str, object] | None
) -> None:
    """D7: гость не создаёт сборники, не лайкает и не ведёт избранное."""
    response = await svc.client.request(method, path, json=body, headers=KEY)
    assert_problem(response, 401, "auth.unauthenticated")
    guest = _bearer(svc.issuer.issue_guest()) | KEY
    response = await svc.client.request(method, path, json=body, headers=guest)
    assert_problem(response, 403, "auth.guest_forbidden")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/v1/collections", {"domain": "film", "title": "x", "kind": "curated"}),
        ("POST", "/v1/collections", {"domain": "film", "title": "x", "author_id": USER}),
        ("POST", "/v1/collections", {"domain": "anime", "title": "x"}),
        ("POST", "/v1/collections", {"domain": "film", "title": ""}),
        (
            "PUT",
            f"/v1/collections/{CID}/draft",
            {"base_revision": 1, "body": {**BODY, "featured": True}},
        ),
        (
            "PUT",
            f"/v1/collections/{CID}/draft",
            {"base_revision": 1, "body": {**BODY, "status": "published"}},
        ),
        (
            "PUT",
            f"/v1/collections/{CID}/draft",
            {"base_revision": 1, "body": {**BODY, "items": [{"media_item_id": ITEM}] * 201}},
        ),
        (
            "PUT",
            f"/v1/collections/{CID}/draft",
            {"base_revision": 1, "body": {**BODY, "default_mode_key": "film.random.frame"}},
        ),
        ("POST", f"/v1/collections/{CID}/publish", {"draft_revision": 0}),
    ],
)
async def test_public_schemas_forbid_mass_assignment(
    svc: Any, method: str, path: str, body: dict[str, object]
) -> None:
    """§12 п. 2: `kind`, `author_id`, `featured`, `status` из публичного API не принимаются."""
    response = await svc.client.request(method, path, json=body, headers=svc.user().mutate())
    assert_problem(response, 422, "request.validation_failed")


async def test_mutations_require_idempotency_key(svc: Any) -> None:
    response = await svc.client.put(f"/v1/collections/{CID}/like", headers=svc.user().headers)
    assert_problem(response, 422, "idempotency.key_required")


async def test_cookie_mutation_requires_csrf_and_origin(svc: Any) -> None:
    user = svc.user()
    token = user.headers["Authorization"].removeprefix("Bearer ")
    svc.client.cookies.set("gv_access", token)
    try:
        body = {"domain": "film", "title": "Кино"}
        response = await svc.client.post("/v1/collections", json=body, headers=KEY)
        assert_problem(response, 403, "auth.csrf_failed")
        svc.client.cookies.set("gv_csrf", "csrf-token-value")
        ok_headers = {
            "Origin": "https://oncue.test",
            "X-CSRF-Token": "csrf-token-value",
            "Idempotency-Key": f"k-{uuid7().hex}",
        }
        response = await svc.client.post("/v1/collections", json=body, headers=ok_headers)
        assert response.status_code == 201, response.text
        evil = ok_headers | {"Origin": "https://evil.test", "Idempotency-Key": f"k-{uuid7().hex}"}
        response = await svc.client.post("/v1/collections", json=body, headers=evil)
        assert_problem(response, 403, "auth.csrf_failed")
    finally:
        svc.client.cookies.clear()


@pytest.mark.parametrize(("method", "path", "body", "perm"), ADMIN_ROUTES)
async def test_admin_routes_require_specific_perm(
    svc: Any, method: str, path: str, body: dict[str, object] | None, perm: str
) -> None:
    """`admin.access` + право из миграции identity 0007; с правами — до бизнес-логики."""
    for token in (
        svc.issuer.issue(perms=["admin.access"]),
        svc.issuer.issue(perms=[perm]),
        svc.issuer.issue_guest(),
    ):
        response = await svc.client.request(method, path, json=body, headers=_bearer(token) | KEY)
        assert_problem(response, 403, "auth.forbidden")
    token = svc.issuer.issue(perms=["admin.access", perm])
    headers = _bearer(token) | {"Idempotency-Key": f"k-{uuid7().hex}"}
    response = await svc.client.request(method, path, json=body, headers=headers)
    # Сборника CID нет: 404; создание с неизвестной единицей — 422; ограничение — 200.
    assert response.status_code in (200, 404, 422), response.text
    assert response.status_code != 403
