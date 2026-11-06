"""Внутренняя ручка первых единиц сборников под обложки (``POST /internal/v1/cover-items``)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from gv_core.ids import uuid7
from gv_testkit import assert_problem

pytestmark = pytest.mark.component

PATH = "/internal/v1/cover-items"


def _service(
    svc: Any, scopes: tuple[str, ...] = ("covers.read",), audience: str = "collections"
) -> str:
    return str(svc.issuer.issue_service("bff_web", audience, scopes))


async def _post(svc: Any, ids: list[Any], token: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token or _service(svc)}"}
    return await svc.client.post(
        PATH, json={"collection_ids": [str(i) for i in ids]}, headers=headers
    )


async def test_requires_service_token_with_scope(svc: Any) -> None:
    body = {"collection_ids": [str(uuid7())]}
    assert_problem(await svc.client.post(PATH, json=body), 401, "auth.unauthenticated")
    for token in (
        svc.issuer.issue(),
        svc.issuer.issue_guest(),
        _service(svc, audience="game"),
    ):
        response = await svc.client.post(
            PATH, json=body, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401, response.text
    no_scope = await _post(svc, [uuid7()], _service(svc, scopes=("rounds.plan",)))
    assert_problem(no_scope, 403, "auth.forbidden")


async def test_returns_first_item_by_position(svc: Any) -> None:
    items = await svc.add_items(5)
    published = await svc.published(svc.user(), items=items)
    response = await _post(svc, [published["collection_id"]])
    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [{"collection_id": published["collection_id"], "media_item_id": str(items[0])}]
    }
    assert response.headers["cache-control"] == "no-store"


async def test_only_published_public_collections(svc: Any) -> None:
    author = svc.user()
    public = (await svc.published(author))["collection_id"]
    unlisted = (await svc.published(author, visibility="unlisted"))["collection_id"]
    hidden = (await svc.published(author))["collection_id"]
    deleted = (await svc.published(author))["collection_id"]
    draft = (await svc.create(author))["collection_id"]
    await svc.sql_exec(
        "UPDATE collections SET status = 'hidden', hidden_at = now(), hidden_reason = 'x' "
        "WHERE id = :i",
        i=hidden,
    )
    await svc.sql_exec(
        "UPDATE collections SET status = 'deleted', deleted_at = now() WHERE id = :i", i=deleted
    )

    response = await _post(svc, [public, unlisted, hidden, deleted, draft, uuid7()])
    assert response.status_code == 200, response.text
    assert [i["collection_id"] for i in response.json()["items"]] == [public]


async def test_duplicates_collapse_and_limits(svc: Any) -> None:
    published = await svc.published(svc.user())
    cid = published["collection_id"]
    dup = await _post(svc, [cid, cid])
    assert len(dup.json()["items"]) == 1

    max_ids = 50
    assert (await _post(svc, [uuid7() for _ in range(max_ids)])).status_code == 200
    over = await _post(svc, [uuid7() for _ in range(max_ids + 1)])
    assert_problem(over, 422, "collections.too_many_ids")
    assert over.json()["details"] == {"given": max_ids + 1, "max": max_ids}

    assert (await _post(svc, [])).status_code == 422
    schema_cap = await _post(svc, [uuid7() for _ in range(201)])
    assert schema_cap.status_code == 422
