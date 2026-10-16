"""Кто что видит (D6, §12 п. 1): матрица состава и скрытого сборника на каждого субъекта."""

from __future__ import annotations

from typing import Any

import pytest

from gv_testkit import assert_problem

pytestmark = pytest.mark.component

EMOJI = "🎬🍿🎥🎭🌟🎻"


@pytest.fixture
async def world(svc: Any) -> dict[str, Any]:
    """Опубликованный сборник со своими эмодзи и субъекты всех видов."""
    author = svc.user()
    items = await svc.add_items(5)
    custom = {"media_item_id": str(items[0]), "custom": {"emoji": EMOJI}}
    draft = await svc.create(author)
    cid = draft["collection_id"]
    body = {
        "title": "Кино · кадры",
        "default_mode_key": "film.collection.frame",
        "items": [custom, *({"media_item_id": str(i)} for i in items[1:])],
    }
    saved = await svc.save(author, cid, body, base=1)
    detail = (await svc.publish(author, cid, saved.json()["revision"])).json()
    completed = svc.user()
    await svc.finish_game(completed, cid, version=1)
    return {
        "cid": cid,
        "detail": detail,
        "subjects": {
            "anonymous": None,
            "guest": svc.guest(),
            "player": svc.user(),
            "completed": completed,
            "author": author,
            "admin": svc.admin("admin.access", "collections.view"),
        },
    }


# субъект → (items видны, свои эмодзи видны, me есть)
MATRIX = {
    "anonymous": (False, False, False),
    "guest": (False, False, False),
    "player": (False, False, True),
    "completed": (True, False, True),
    "author": (True, True, True),
    "admin": (True, True, True),
}


@pytest.mark.parametrize("subject", list(MATRIX))
async def test_items_visibility_matrix(svc: Any, world: dict[str, Any], subject: str) -> None:
    user = world["subjects"][subject]
    headers = user.headers if user is not None else {}
    response = await svc.client.get(f"/v1/collections/{world['cid']}", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    items_visible, custom_visible, has_me = MATRIX[subject]
    assert data["items_visible"] is items_visible
    assert (data["items"] is not None) is items_visible
    assert data["items_count"] == 5
    if items_visible:
        assert len(data["items"]) == 5 and all(i["available"] for i in data["items"])
        assert (data["items"][0]["custom"] == {"emoji": EMOJI}) is custom_visible
        if not custom_visible:
            assert data["items"][0]["custom"] is None
    else:
        # Античит/спойлеры: ни id, ни названий единиц в ответе нет.
        text = response.text
        assert all(i["media_item_id"] not in text for i in world["detail"]["items"])
        assert all(i["title"] not in text for i in world["detail"]["items"])
    assert (data["me"] is not None) is has_me
    if subject == "completed":
        assert data["me"]["completed"] is True
    if subject == "author":
        assert data["me"]["is_author"] is True


async def test_new_version_closes_items_until_completed_again(
    svc: Any, world: dict[str, Any]
) -> None:
    """«Пройден» — по текущей версии: после новой версии состав снова закрыт."""
    author, completed = world["subjects"]["author"], world["subjects"]["completed"]
    cid = world["cid"]
    items = [i["media_item_id"] for i in world["detail"]["items"]]
    body = {
        "title": "Кино · кадры",
        "default_mode_key": "film.collection.frame",
        "items": [{"media_item_id": i} for i in items],
    }
    saved = await svc.save(author, cid, body, base=0)
    assert (await svc.publish(author, cid, saved.json()["revision"])).json()["version"] == 2
    data = (await svc.client.get(f"/v1/collections/{cid}", headers=completed.headers)).json()
    assert data["items_visible"] is False and data["me"]["completed"] is False
    await svc.finish_game(completed, cid, version=2)
    data = (await svc.client.get(f"/v1/collections/{cid}", headers=completed.headers)).json()
    assert data["items_visible"] is True


@pytest.mark.parametrize("subject", list(MATRIX))
async def test_hidden_collection_visible_only_to_author_and_admin(
    svc: Any, world: dict[str, Any], subject: str
) -> None:
    moderator = svc.admin("admin.access", "collections.unpublish")
    hidden = await svc.client.post(
        f"/admin/v1/collections/{world['cid']}/hide",
        json={"reason": "спам"},
        headers=moderator.mutate(),
    )
    assert hidden.status_code == 200, hidden.text
    user = world["subjects"][subject]
    response = await svc.client.get(
        f"/v1/collections/{world['cid']}", headers=user.headers if user else {}
    )
    if subject in ("author", "admin"):
        assert response.status_code == 200
        assert response.json()["status"] == "hidden"
    else:
        assert_problem(response, 404, "collections.not_found")


async def test_unlisted_is_reachable_by_link_but_not_in_catalog(svc: Any) -> None:
    detail = await svc.published(svc.user(), visibility="unlisted")
    cid = detail["collection_id"]
    assert (await svc.client.get(f"/v1/collections/{cid}")).json()["visibility"] == "unlisted"
    page = (await svc.client.get("/v1/collections", params={"sort": "new"})).json()
    assert cid not in {i["collection_id"] for i in page["items"]}


async def test_draft_only_collection_has_no_public_card(svc: Any) -> None:
    author = svc.user()
    cid = (await svc.create(author))["collection_id"]
    for headers in ({}, author.headers, svc.admin().headers):
        assert_problem(
            await svc.client.get(f"/v1/collections/{cid}", headers=headers),
            404,
            "collections.not_found",
        )
