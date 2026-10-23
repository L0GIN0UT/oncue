"""«Рейтинг» (этап 4b, план этапа 4 §10): ladder-ы, версии, черновик, публикация, откат, сезоны.

Заглушка rating проверяет и ответы (по contracts/openapi/rating.json), и запросы админки:
JSON-тело по схемам запросов и ``Idempotency-Key`` у каждой мутации.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from admin_service.audit.models import AuditEntry

pytestmark = [pytest.mark.component, pytest.mark.django_db]

SEASON = uuid.uuid4()
USER = uuid.uuid4()
NOW = "2026-10-22T10:00:00Z"
LADDERS = "/admin/v1/ladders"
LADDER_ONLY = ("admin.access", "rating.ladders.publish")
SEASON_ONLY = ("admin.access", "rating.seasons.manage")
CONFIG = {"key": "film", "domain": "film", "elo": {"start": 1000, "k": 32}}


def _card(version: int, status: str) -> dict[str, Any]:
    return {
        "version": version,
        "status": status,
        "created_at": NOW,
        "created_by": str(USER),
        "published_at": NOW if status != "draft" else None,
    }


def _season(status: str = "finished") -> dict[str, Any]:
    return {
        "season_id": str(SEASON),
        "ladder": "film",
        "number": 2,
        "status": status,
        "starts_at": NOW,
        "ends_at": NOW,
        "finished_at": NOW if status == "finished" else None,
    }


def _key() -> str:
    return str(uuid.uuid4())


def _stub_ladder_page(rating: Any, *, draft: bool = True) -> None:
    items = [_card(1, "archived"), _card(2, "published")]
    if draft:
        items.insert(0, _card(3, "draft"))
    rating.route("GET", f"{LADDERS}/film/versions").respond(200, json={"items": items})
    rating.route("GET", LADDERS).respond(
        200,
        json={
            "items": [
                {
                    "key": "film",
                    "published_version": 2,
                    "draft_version": 3 if draft else None,
                    "season_id": str(SEASON),
                }
            ]
        },
    )


def _version(rating: Any, number: int, status: str, config: dict[str, Any]) -> None:
    rating.route("GET", f"{LADDERS}/film/versions/{number}").respond(
        200, json={"card": _card(number, status), "config": config}
    )


# ------------------------------------------------------------------------------ доступ


def test_section_requires_ladders_perm(client: Any, login: Any, moderator_perms: Any) -> None:
    login(perms=moderator_perms, roles=("moderator",))
    assert client.get("/rating/").status_code == 403
    assert client.get("/rating/film/").status_code == 403
    assert client.get("/rating/film/edit/").status_code == 403
    for path in ("publish", "rollback"):
        response = client.post(
            f"/rating/film/{path}/", {"reason": "тест прав", "idempotency_key": _key()}
        )
        assert response.status_code == 403
    assert 'href="/rating/"' not in client.get("/").content.decode()
    assert not AuditEntry.objects.exists()


def test_menu_shows_rating_with_perm(client: Any, login: Any) -> None:
    login()
    assert 'href="/rating/"' in client.get("/").content.decode()


def test_season_actions_need_seasons_manage_on_server(client: Any, login: Any) -> None:
    login(perms=LADDER_ONLY, roles=("moderator",))
    body = {"reason": "тест прав", "idempotency_key": _key()}
    assert client.post("/rating/film/seasons/create/", body).status_code == 403
    assert client.post("/rating/film/leaderboard/rebuild/", body).status_code == 403
    close = f"/rating/film/seasons/{SEASON}/close/"
    assert client.post(close, body | {"confirm": "on"}).status_code == 403
    assert client.get(close).status_code == 403
    assert not AuditEntry.objects.exists()


def test_ladder_perm_needed_for_publish_even_with_seasons_perm(client: Any, login: Any) -> None:
    login(perms=SEASON_ONLY, roles=("moderator",))
    response = client.post(
        "/rating/film/publish/", {"version": 3, "reason": "тест", "idempotency_key": _key()}
    )
    assert response.status_code == 403


def test_buttons_hidden_without_seasons_perm(client: Any, login: Any, rating: Any) -> None:
    login(perms=LADDER_ONLY, roles=("moderator",))
    _stub_ladder_page(rating)
    _version(rating, 2, "published", CONFIG)
    _version(rating, 3, "draft", CONFIG)
    page = client.get("/rating/film/").content.decode()
    assert "Опубликовать v3" in page
    assert "Создать сезон" not in page
    assert "Пересобрать лидерборд" not in page
    assert "Закрыть текущий сезон" not in page


def test_invalid_ladder_key_is_404(client: Any, login: Any) -> None:
    login()
    assert client.get("/rating/Film/").status_code == 404
    assert client.get("/rating/1abc/edit/").status_code == 404


# ------------------------------------------------------------------------------ чтение


def test_ladder_list(client: Any, login: Any, rating: Any) -> None:
    login()
    _stub_ladder_page(rating)
    page = client.get("/rating/").content.decode()
    assert 'href="/rating/film/"' in page
    assert "v2" in page
    assert "v3" in page


def test_ladder_list_survives_rating_unavailable(
    client: Any, login: Any, rating: Any, problem: Any
) -> None:
    login()
    rating.route("GET", LADDERS).respond(403, json=problem(403, "auth.forbidden"))
    response = client.get("/rating/")
    assert response.status_code == 200
    assert "Недостаточно прав" in response.content.decode()


def test_detail_shows_versions_and_draft_diff(client: Any, login: Any, rating: Any) -> None:
    login()
    _stub_ladder_page(rating)
    _version(rating, 2, "published", CONFIG)
    _version(rating, 3, "draft", CONFIG | {"elo": {"start": 1000, "k": 24}})
    page = client.get("/rating/film/").content.decode()
    assert "Отличия v2 → v3" in page
    assert "k: 24" in page
    assert f'href="/rating/film/seasons/{SEASON}/close/"' in page


def test_detail_diff_between_arbitrary_versions_and_body(
    client: Any, login: Any, rating: Any
) -> None:
    login()
    _stub_ladder_page(rating, draft=False)
    _version(rating, 1, "archived", CONFIG | {"elo": {"start": 900, "k": 32}})
    _version(rating, 2, "published", CONFIG)
    page = client.get("/rating/film/", {"from": 1, "to": 2, "version": 1}).content.decode()
    assert "Отличия v1 → v2" in page
    assert "start: 900" in page
    assert "Конфиг v1" in page


def test_detail_not_found(client: Any, login: Any, rating: Any, problem: Any) -> None:
    login()
    rating.route("GET", f"{LADDERS}/nope/versions").respond(
        404, json=problem(404, "rating.ladder_not_found")
    )
    assert client.get("/rating/nope/").status_code == 404


# ------------------------------------------------------------------------------ редактор


def test_editor_prefills_from_draft_and_saves(client: Any, login: Any, rating: Any) -> None:
    login()
    _stub_ladder_page(rating)
    _version(rating, 3, "draft", CONFIG)
    page = client.get("/rating/film/edit/").content.decode()
    assert "start: 1000" in page
    assert 'name="idempotency_key"' in page

    key = _key()
    route = rating.route("PUT", f"{LADDERS}/film/draft").respond(
        200, json={"card": _card(4, "draft"), "config": CONFIG}
    )
    text = "key: film\ndomain: film\nelo:\n  start: 1000\n  k: 20\n"
    response = client.post(
        "/rating/film/edit/",
        {"fmt": "yaml", "text": text, "reason": "меняем K", "idempotency_key": key},
    )
    assert response.status_code == 302
    assert response["Location"] == "/rating/film/"
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert json.loads(request.content) == {
        "config": {"key": "film", "domain": "film", "elo": {"start": 1000, "k": 20}},
        "reason": "меняем K",
    }
    entry = AuditEntry.objects.get(action="rating.save_ladder_draft")
    assert (entry.service, entry.target_type, entry.target_id) == ("rating", "ladder", "film")
    assert entry.after == {"version": 4, "status": "draft"}  # тело конфига в аудит не пишется
    assert entry.idempotency_key == key


def test_editor_shows_rating_validation_errors_with_lines(
    client: Any, login: Any, rating: Any, problem: Any
) -> None:
    login()
    text = "key: film\nelo:\n  start: много\n"
    rating.route("PUT", f"{LADDERS}/film/draft").respond_off_contract(
        422,
        json=problem(422, "rating.ladder_invalid")
        | {"details": {"errors": [{"path": "/elo/start", "code": "type"}]}},
    )
    response = client.post(
        "/rating/film/edit/",
        {"fmt": "yaml", "text": text, "reason": "правка", "idempotency_key": _key()},
    )
    page = response.content.decode()
    assert "elo.start" in page
    assert "много" in page  # текст остаётся в форме
    entry = AuditEntry.objects.get(action="rating.save_ladder_draft")
    assert entry.outcome == AuditEntry.Outcome.FAILED
    assert entry.error_code == "rating.ladder_invalid"


def test_editor_rejects_broken_text_and_missing_reason_without_service_call(
    client: Any, login: Any, rating: Any
) -> None:
    login()
    broken = client.post(
        "/rating/film/edit/",
        {"fmt": "yaml", "text": "a: [", "reason": "правка", "idempotency_key": _key()},
    )
    assert broken.status_code == 400
    no_reason = client.post(
        "/rating/film/edit/",
        {"fmt": "yaml", "text": "a: 1", "reason": "", "idempotency_key": _key()},
    )
    assert no_reason.status_code == 400
    assert not AuditEntry.objects.exists()


# ------------------------------------------------------------------------------ версии


def test_publish_sends_version_with_idempotency_key(client: Any, login: Any, rating: Any) -> None:
    login()
    key = _key()
    route = rating.route("POST", f"{LADDERS}/film/publish").respond(
        200, json={"card": _card(3, "published"), "config": CONFIG}
    )
    response = client.post(
        "/rating/film/publish/", {"version": 3, "reason": "релиз", "idempotency_key": key}
    )
    assert response["Location"] == "/rating/film/"
    assert route.calls.last.request.headers["Idempotency-Key"] == key
    assert json.loads(route.calls.last.request.content) == {"version": 3, "reason": "релиз"}
    entry = AuditEntry.objects.get(action="rating.publish_ladder")
    assert entry.before == {"draft_version": 3}
    assert entry.after == {"version": 3, "status": "published"}


def test_publish_conflict_is_shown_and_audited(
    client: Any, login: Any, rating: Any, problem: Any
) -> None:
    login()
    rating.route("POST", f"{LADDERS}/film/publish").respond(
        409, json=problem(409, "rating.version_not_draft", "Версия не черновик")
    )
    _stub_ladder_page(rating)
    _version(rating, 2, "published", CONFIG)
    _version(rating, 3, "draft", CONFIG)
    response = client.post(
        "/rating/film/publish/",
        {"version": 2, "reason": "релиз", "idempotency_key": _key()},
        follow=True,
    )
    assert "Версия не черновик" in response.content.decode()
    entry = AuditEntry.objects.get(action="rating.publish_ladder")
    assert entry.outcome == AuditEntry.Outcome.FAILED
    assert entry.error_code == "rating.version_not_draft"


def test_rollback(client: Any, login: Any, rating: Any) -> None:
    login()
    key = _key()
    route = rating.route("POST", f"{LADDERS}/film/rollback").respond(
        200, json={"card": _card(4, "published"), "config": CONFIG}
    )
    client.post(
        "/rating/film/rollback/", {"to_version": 1, "reason": "откат", "idempotency_key": key}
    )
    assert route.calls.last.request.headers["Idempotency-Key"] == key
    assert json.loads(route.calls.last.request.content) == {"to_version": 1, "reason": "откат"}
    assert AuditEntry.objects.get(action="rating.rollback_ladder").before == {"to_version": 1}


def test_publish_without_reason_is_rejected_locally(client: Any, login: Any, rating: Any) -> None:
    login()
    _stub_ladder_page(rating)
    _version(rating, 2, "published", CONFIG)
    _version(rating, 3, "draft", CONFIG)
    response = client.post(
        "/rating/film/publish/", {"version": 3, "reason": "", "idempotency_key": _key()}
    )
    assert response.status_code == 400
    assert not AuditEntry.objects.exists()


# ------------------------------------------------------------------------------ сезоны


def test_create_season(client: Any, login: Any, rating: Any) -> None:
    login()
    key = _key()
    route = rating.route("POST", f"{LADDERS}/film/seasons").respond(201, json=_season("scheduled"))
    response = client.post(
        "/rating/film/seasons/create/",
        {"reason": "новый сезон", "starts_at": "2026-11-01 00:00", "idempotency_key": key},
    )
    assert response["Location"] == "/rating/film/"
    assert route.calls.last.request.headers["Idempotency-Key"] == key
    assert json.loads(route.calls.last.request.content) == {
        "reason": "новый сезон",
        "starts_at": "2026-11-01T00:00:00+00:00",
    }
    entry = AuditEntry.objects.get(action="rating.create_season")
    assert entry.after == {"season_id": str(SEASON), "number": 2, "status": "scheduled"}


def test_create_season_without_start(client: Any, login: Any, rating: Any) -> None:
    login()
    route = rating.route("POST", f"{LADDERS}/film/seasons").respond(201, json=_season("active"))
    client.post("/rating/film/seasons/create/", {"reason": "старт", "idempotency_key": _key()})
    assert json.loads(route.calls.last.request.content) == {"reason": "старт"}


def test_close_season_requires_confirmation(client: Any, login: Any, rating: Any) -> None:
    login()
    page = client.get(f"/rating/film/seasons/{SEASON}/close/").content.decode()
    assert "необратимо" in page
    response = client.post(
        f"/rating/film/seasons/{SEASON}/close/",
        {"reason": "конец сезона", "idempotency_key": _key()},
    )
    assert response.status_code == 400  # без отметки в rating ничего не уходит
    assert not AuditEntry.objects.exists()


def test_close_season(client: Any, login: Any, rating: Any) -> None:
    login()
    key = _key()
    route = rating.route("POST", f"/admin/v1/seasons/{SEASON}/close").respond(
        200, json=_season("finished")
    )
    response = client.post(
        f"/rating/film/seasons/{SEASON}/close/",
        {"reason": "конец сезона", "confirm": "on", "idempotency_key": key},
    )
    assert response["Location"] == "/rating/film/"
    assert route.calls.last.request.headers["Idempotency-Key"] == key
    assert json.loads(route.calls.last.request.content) == {"reason": "конец сезона"}
    entry = AuditEntry.objects.get(action="rating.close_season")
    assert (entry.target_type, entry.target_id) == ("season", str(SEASON))


def test_close_season_refusal_is_audited(
    client: Any, login: Any, rating: Any, problem: Any
) -> None:
    login()
    rating.route("POST", f"/admin/v1/seasons/{SEASON}/close").respond(
        409, json=problem(409, "rating.season_not_active", "Сезон уже закрыт")
    )
    _stub_ladder_page(rating)
    _version(rating, 2, "published", CONFIG)
    _version(rating, 3, "draft", CONFIG)
    response = client.post(
        f"/rating/film/seasons/{SEASON}/close/",
        {"reason": "конец сезона", "confirm": "on", "idempotency_key": _key()},
        follow=True,
    )
    assert "Сезон уже закрыт" in response.content.decode()
    entry = AuditEntry.objects.get(action="rating.close_season")
    assert entry.outcome == AuditEntry.Outcome.FAILED
    assert entry.error_code == "rating.season_not_active"


def test_rebuild_leaderboard(client: Any, login: Any, rating: Any) -> None:
    login()
    key = _key()
    route = rating.route("POST", f"{LADDERS}/film/leaderboard/rebuild").respond(
        202, json={"ladder": "film", "season_id": str(SEASON), "status": "accepted"}
    )
    client.post(
        "/rating/film/leaderboard/rebuild/", {"reason": "потеря Redis", "idempotency_key": key}
    )
    assert route.calls.last.request.headers["Idempotency-Key"] == key
    assert json.loads(route.calls.last.request.content) == {"reason": "потеря Redis"}
    entry = AuditEntry.objects.get(action="rating.rebuild_leaderboard")
    assert entry.after == {"season_id": str(SEASON), "status": "accepted"}


def test_get_is_not_allowed_on_mutations(client: Any, login: Any) -> None:
    login()
    for path in ("publish", "rollback", "seasons/create", "leaderboard/rebuild"):
        assert client.get(f"/rating/film/{path}/").status_code == 405
