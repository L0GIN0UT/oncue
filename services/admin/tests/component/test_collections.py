"""«Сборники» (этап 3b, D14): список, карточка, скрытие, «рекомендуемое», ограничение автора."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from admin_service.audit.models import AuditEntry

pytestmark = [pytest.mark.component, pytest.mark.django_db]

CID = uuid.uuid4()
AUTHOR = uuid.uuid4()
ITEM_A, ITEM_B = uuid.uuid4(), uuid.uuid4()
API = f"/admin/v1/collections/{CID}"
PAGE = f"/collections/{CID}/"
NOW = "2026-10-16T10:00:00Z"


def _out(**kw: Any) -> dict[str, Any]:
    return {
        "collection_id": str(CID),
        "domain": "film",
        "kind": "ugc",
        "status": "published",
        "visibility": "public",
        "title": "Кино · кадры",
        "author": {"kind": "user", "user_id": str(AUTHOR), "nickname": "author_a"},
        "author_restricted": False,
        "featured": False,
        "featured_rank": None,
        "hidden": None,
        "items_count": 2,
        "likes_count": 3,
        "plays_count": 7,
        "difficulty": None,
        "version": 4,
        "published_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    } | kw


def _detail(**kw: Any) -> dict[str, Any]:
    return (
        _out(
            description="Восемь фильмов",
            default_mode_key="film.collection.frame",
            tags=["classic"],
            cover={"preset": "night"},
            draft_revision=None,
            items=[
                {
                    "position": 0,
                    "media_item_id": str(ITEM_A),
                    "title": "Сталкер",
                    "year": 1979,
                    "available": True,
                    "custom": {"emoji": "🛤️🌾🐕💧🚪🌀"},
                },
                {
                    "position": 1,
                    "media_item_id": str(ITEM_B),
                    "title": "Солярис",
                    "year": 1972,
                    "available": False,
                    "custom": None,
                },
            ],
        )
        | kw
    )


def _key() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------- права и меню


def test_menu_and_list_hidden_without_view_perm(client: Any, login: Any, collections: Any) -> None:
    login(perms=("admin.access", "game.daily.view"), roles=("content",))
    route = collections.route("GET", "/admin/v1/collections").respond(
        200, json={"items": [], "next_cursor": None}
    )
    assert 'href="/collections/"' not in client.get("/").content.decode()
    assert client.get("/collections/").status_code == 403
    assert client.get(PAGE).status_code == 403
    assert not route.called


def test_menu_shows_collections_with_view_perm(client: Any, login: Any) -> None:
    login()
    assert 'href="/collections/"' in client.get("/").content.decode()


# ---------------------------------------------------------------------------- список


def test_list_forwards_filters_and_shows_rows(client: Any, login: Any, collections: Any) -> None:
    login()
    hidden = _out(
        status="hidden",
        author_restricted=True,
        hidden={"reason": "спам", "actor_id": str(uuid.uuid4()), "at": NOW},
    )
    curated = _out(
        collection_id=str(uuid.uuid4()),
        kind="curated",
        title="Классика",
        author={"kind": "curator", "user_id": None, "nickname": None},
        featured=True,
        featured_rank=2,
    )
    route = collections.route("GET", "/admin/v1/collections").respond(
        200, json={"items": [hidden, curated], "next_cursor": "abc_DEF-1"}
    )
    page = client.get("/collections/", {"q": "кадры", "status": "hidden", "kind": "ugc"})
    params = dict(route.calls.last.request.url.params)
    assert params == {"q": "кадры", "status": "hidden", "kind": "ugc"}
    html = page.content.decode()
    assert f'href="/collections/{CID}/"' in html
    assert "<strong>скрыт</strong>" in html and "(ограничен)" in html
    assert "Guessverse" in html and "порядок 2" in html
    assert "cursor=abc_DEF-1" in html and "q=%D0%BA" in html  # фильтры сохраняются в «Дальше»
    assert 'action="/collections/new/"' in html  # есть collections.curate


def test_list_service_error_is_shown(client: Any, login: Any, collections: Any) -> None:
    login()
    collections.route("GET", "/admin/v1/collections").respond(
        503, json={"status": 503, "code": "dependency.unavailable", "title": "x", "type": "t"}
    )
    page = client.get("/collections/")
    assert page.status_code == 200
    assert "Сервис временно недоступен" in page.content.decode()


# ---------------------------------------------------------------------------- карточка


def test_detail_shows_items_with_emoji_and_actions(
    client: Any, login: Any, collections: Any
) -> None:
    login()
    collections.route("GET", API).respond(200, json=_detail())
    html = client.get(PAGE).content.decode()
    assert "🛤️🌾🐕💧🚪🌀" in html  # свои эмодзи видны админу
    assert f'href="/catalog/items/{ITEM_A}/"' in html
    assert "<strong>нет</strong>" in html  # Солярис недоступен
    for action in ("hide", "featured", "author-restriction"):
        assert f'action="/collections/{CID}/{action}/"' in html
    assert f"/collections/{CID}/edit/" not in html  # UGC правит только автор


def test_detail_not_found(client: Any, login: Any, collections: Any, problem: Any) -> None:
    login()
    collections.route("GET", API).respond(404, json=problem(404, "collections.not_found"))
    response = client.get(PAGE)
    assert response.status_code == 404
    assert "Сборник не найден" in response.content.decode()


def test_moderator_sees_only_hide(
    client: Any, login: Any, collections: Any, moderator_perms: tuple[str, ...]
) -> None:
    login(perms=moderator_perms, roles=("moderator",))
    collections.route("GET", API).respond(200, json=_detail())
    featured = collections.route("PUT", f"{API}/featured").respond(200, json=_out())
    html = client.get(PAGE).content.decode()
    assert f'action="/collections/{CID}/hide/"' in html
    assert "/featured/" not in html and "/author-restriction/" not in html
    response = client.post(
        f"{PAGE}featured/", {"featured": "on", "reason": "лучшее", "idempotency_key": _key()}
    )
    assert response.status_code == 403
    assert not featured.called
    assert not AuditEntry.objects.exists()


# ---------------------------------------------------------------------------- скрытие


def test_hide_sends_reason_and_audits_status(client: Any, login: Any, collections: Any) -> None:
    login()
    key = _key()
    collections.route("GET", API).respond(200, json=_detail())
    route = collections.route("POST", f"{API}/hide").respond(
        200,
        json=_out(
            status="hidden", version=5, hidden={"reason": "спам", "actor_id": None, "at": NOW}
        ),
    )
    response = client.post(f"{PAGE}hide/", {"reason": "спам в названии", "idempotency_key": key})
    assert response.status_code == 302
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert json.loads(request.content) == {"reason": "спам в названии"}
    entry = AuditEntry.objects.get(action="collections.hide")
    assert (entry.service, entry.target_type, entry.target_id) == (
        "collections",
        "collection",
        str(CID),
    )
    assert entry.before == {"status": "published"}
    assert entry.after == {"status": "hidden", "version": 5}


def test_unhide_refusal_is_explained_and_audited(
    client: Any, login: Any, collections: Any, problem: Any
) -> None:
    login()
    collections.route("GET", API).respond(200, json=_detail(status="draft"))
    collections.route("POST", f"{API}/unhide").respond(
        409, json=problem(409, "collections.not_published")
    )
    response = client.post(
        f"{PAGE}unhide/", {"reason": "вернуть", "idempotency_key": _key()}, follow=True
    )
    assert "ещё не опубликован" in response.content.decode()
    entry = AuditEntry.objects.get(action="collections.unhide")
    assert entry.error_code == "collections.not_published"
    assert entry.after is None


def test_hide_without_reason_is_form_error(client: Any, login: Any, collections: Any) -> None:
    login()
    collections.route("GET", API).respond(200, json=_detail())
    route = collections.route("POST", f"{API}/hide").respond(200, json=_out(status="hidden"))
    response = client.post(f"{PAGE}hide/", {"reason": "", "idempotency_key": _key()})
    assert response.status_code == 400
    assert not route.called


# ---------------------------------------------------------------------------- рекомендуемое


def test_featured_sends_rank_and_audits_flags(client: Any, login: Any, collections: Any) -> None:
    login()
    collections.route("GET", API).respond(200, json=_detail())
    route = collections.route("PUT", f"{API}/featured").respond(
        200, json=_out(featured=True, featured_rank=3)
    )
    client.post(
        f"{PAGE}featured/",
        {"featured": "on", "rank": "3", "reason": "в подборку", "idempotency_key": _key()},
    )
    assert json.loads(route.calls.last.request.content) == {
        "featured": True,
        "rank": 3,
        "reason": "в подборку",
    }
    entry = AuditEntry.objects.get(action="collections.set_featured")
    assert entry.before == {"featured": False, "featured_rank": None}
    assert entry.after == {"featured": True, "featured_rank": 3}


def test_unfeature_drops_rank(client: Any, login: Any, collections: Any) -> None:
    login()
    collections.route("GET", API).respond(200, json=_detail(featured=True, featured_rank=3))
    route = collections.route("PUT", f"{API}/featured").respond(200, json=_out())
    client.post(f"{PAGE}featured/", {"rank": "3", "reason": "убрать", "idempotency_key": _key()})
    assert json.loads(route.calls.last.request.content) == {
        "featured": False,
        "rank": None,
        "reason": "убрать",
    }


def test_featured_rank_out_of_range_is_form_error(
    client: Any, login: Any, collections: Any
) -> None:
    login()
    collections.route("GET", API).respond(200, json=_detail())
    route = collections.route("PUT", f"{API}/featured").respond(200, json=_out())
    response = client.post(
        f"{PAGE}featured/",
        {"featured": "on", "rank": "1001", "reason": "в подборку", "idempotency_key": _key()},
    )
    assert response.status_code == 400
    assert not route.called


# ---------------------------------------------------------------------------- автор


def test_restrict_author_from_collection_and_audit(
    client: Any, login: Any, collections: Any
) -> None:
    login()
    collections.route("GET", API).respond(200, json=_detail())
    route = collections.route("POST", f"/admin/v1/authors/{AUTHOR}/restriction").respond(
        200,
        json={
            "user_id": str(AUTHOR),
            "restricted": True,
            "reason": "спам",
            "actor_id": None,
            "at": NOW,
        },
    )
    # user_id в форме нет: автор берётся из карточки сборника на сервере.
    client.post(
        f"{PAGE}author-restriction/",
        {"restricted": "1", "reason": "спам", "idempotency_key": _key()},
    )
    assert json.loads(route.calls.last.request.content) == {"reason": "спам"}
    entry = AuditEntry.objects.get(action="collections.restrict_author")
    assert (entry.target_type, entry.target_id) == ("user", str(AUTHOR))
    assert entry.before == {"restricted": False}
    assert entry.after == {"restricted": True}


def test_unrestrict_author_uses_delete(client: Any, login: Any, collections: Any) -> None:
    login(perms=("admin.access", "collections.view", "collections.restrict_author"))
    collections.route("GET", API).respond(200, json=_detail(author_restricted=True))
    route = collections.route("DELETE", f"/admin/v1/authors/{AUTHOR}/restriction").respond(
        200, json={"user_id": str(AUTHOR), "restricted": False}
    )
    client.post(
        f"{PAGE}author-restriction/",
        {"restricted": "0", "reason": "исправился", "idempotency_key": _key()},
    )
    assert route.called
    entry = AuditEntry.objects.get(action="collections.unrestrict_author")
    assert (entry.before, entry.after) == ({"restricted": True}, {"restricted": False})


def test_curated_collection_has_no_author_to_restrict(
    client: Any, login: Any, collections: Any
) -> None:
    login()
    curated = _detail(kind="curated", author={"kind": "curator", "user_id": None, "nickname": None})
    collections.route("GET", API).respond(200, json=curated)
    html = client.get(PAGE).content.decode()
    assert "/author-restriction/" not in html
    assert f'href="/collections/{CID}/edit/"' in html
    response = client.post(
        f"{PAGE}author-restriction/",
        {"restricted": "1", "reason": "спам", "idempotency_key": _key()},
        follow=True,
    )
    assert "нет автора-пользователя" in response.content.decode()
    assert not AuditEntry.objects.exists()
