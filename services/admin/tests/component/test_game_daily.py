"""Раздел «Наборы дня»: календарь, замена единицы и перегенерация через admin-API game."""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from admin_service.apps.game_daily.views import today
from admin_service.audit.models import AuditEntry

pytestmark = [pytest.mark.component, pytest.mark.django_db]

KEY = "film.daily.frame"
DAY = date(2026, 10, 12)
OLD, NEW = uuid.uuid4(), uuid.uuid4()
ITEM_URL = f"/game/daily/{KEY}/{DAY.isoformat()}/items/1/"
API_ITEM = f"/admin/v1/daily-sets/{KEY}/{DAY.isoformat()}/items/1"


def _set(day: date = DAY, status: str = "scheduled", second: uuid.UUID = OLD) -> dict[str, Any]:
    return {
        "mode_key": KEY,
        "day": day.isoformat(),
        "mode_version": 2,
        "status": status,
        "items": [
            {
                "index": 0,
                "media_item_id": str(uuid.uuid4()),
                "title": "Сталкер",
                "year": 1979,
                "playable": True,
            },
            {
                "index": 1,
                "media_item_id": str(second),
                "title": "Кадр с надписью",
                "year": 2001,
                "playable": True,
            },
        ],
    }


def _candidate(
    item_id: uuid.UUID, *, playable: bool | None = True, status: str = "active"
) -> dict[str, Any]:
    return {
        "media_item_id": str(item_id),
        "domain": "film",
        "kind": "film",
        "title": "Брат",
        "year": 1997,
        "status": status,
        "playable": playable,
    }


def _key() -> str:
    return str(uuid.uuid4())


def test_calendar_shows_sets_and_actions_by_status(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("GET", "/admin/v1/mode-configs").respond(200, json=[])
    locked_day, failed_day = DAY - timedelta(days=1), DAY + timedelta(days=1)
    route = game.route("GET", "/admin/v1/daily-sets").respond(
        200,
        json=[
            _set(locked_day, "locked"),
            _set(DAY, "scheduled"),
            {**_set(failed_day, "failed"), "items": []},
        ],
    )
    page = client.get(
        "/game/daily/",
        {"mode_key": KEY, "start": locked_day.isoformat(), "end": failed_day.isoformat()},
    ).content.decode()
    assert dict(route.calls.last.request.url.params) == {
        "mode_key": KEY,
        "from": locked_day.isoformat(),
        "to": failed_day.isoformat(),
    }
    assert f"/game/daily/{KEY}/{DAY.isoformat()}/items/0/" in page
    assert f"/game/daily/{KEY}/{locked_day.isoformat()}/items/" not in page
    assert f"/game/daily/{KEY}/{locked_day.isoformat()}/regenerate/" not in page
    assert f"/game/daily/{KEY}/{DAY.isoformat()}/regenerate/" in page
    assert f"/game/daily/{KEY}/{failed_day.isoformat()}/regenerate/" in page
    assert "(только чтение)" in page


def test_calendar_defaults_to_week_from_today(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("GET", "/admin/v1/mode-configs").respond(200, json=[])
    route = game.route("GET", "/admin/v1/daily-sets").respond(200, json=[])
    client.get("/game/daily/", {"mode_key": KEY})
    start = today()
    assert dict(route.calls.last.request.url.params) == {
        "mode_key": KEY,
        "from": start.isoformat(),
        "to": (start + timedelta(days=6)).isoformat(),
    }


@pytest.mark.parametrize(
    ("start", "end"),
    [("2026-10-10", "2026-10-09"), ("2026-01-01", "2026-12-31")],
)
def test_calendar_rejects_bad_range(
    client: Any, login: Any, game: Any, start: str, end: str
) -> None:
    login()
    game.route("GET", "/admin/v1/mode-configs").respond(200, json=[])
    route = game.route("GET", "/admin/v1/daily-sets").respond(200, json=[])
    response = client.get("/game/daily/", {"mode_key": KEY, "start": start, "end": end})
    assert response.status_code == 400
    assert not route.called


def test_viewer_without_curate_sees_no_actions(client: Any, login: Any, game: Any) -> None:
    login(perms=("admin.access", "game.daily.view"), roles=("support",))
    route = game.route("GET", "/admin/v1/daily-sets").respond(200, json=[_set()])
    page = client.get("/game/daily/", {"mode_key": KEY}).content.decode()
    assert route.called
    assert "/items/" not in page
    assert "/regenerate/" not in page
    assert client.get(ITEM_URL).status_code == 403


def test_replace_page_searches_playable_candidates(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("GET", "/admin/v1/daily-sets").respond(200, json=[_set()])
    blocked = uuid.uuid4()
    search = game.route("GET", "/admin/v1/projection/items").respond(
        200, json=[_candidate(NEW), _candidate(blocked, status="blocked")]
    )
    page = client.get(ITEM_URL, {"q": "брат"}).content.decode()
    assert dict(search.calls.last.request.url.params) == {
        "q": "брат",
        "domain": "film",
        "playable_for_mode": KEY,
        "limit": "20",
    }
    assert "Кадр с надписью" in page  # текущая единица
    assert f'value="{NEW}" id="cand-1">' in page
    assert f'value="{blocked}" id="cand-2" disabled>' in page


def test_replace_sends_key_and_reason_and_audits(client: Any, login: Any, game: Any) -> None:
    login()
    key = _key()
    game.route("GET", "/admin/v1/daily-sets").respond(200, json=[_set()])
    route = game.route("PUT", API_ITEM).respond(200, json=_set(second=NEW))
    response = client.post(
        ITEM_URL,
        {"media_item_id": str(NEW), "reason": "надпись на кадре", "idempotency_key": key},
    )
    assert response.status_code == 302
    assert response["Location"] == f"/game/daily/?mode_key={KEY}&start={DAY.isoformat()}"
    assert route.calls.last.request.headers["Idempotency-Key"] == key
    assert json.loads(route.calls.last.request.content) == {
        "media_item_id": str(NEW),
        "reason": "надпись на кадре",
    }
    entry = AuditEntry.objects.get(action="game.replace_daily_item")
    assert entry.target_id == f"{KEY}/{DAY.isoformat()}"
    assert entry.before == {"index": 1, "media_item_id": str(OLD)}
    assert entry.after == {"index": 1, "media_item_id": str(NEW)}


def test_replace_in_locked_set_shows_error_and_audits_refusal(
    client: Any, login: Any, game: Any, problem: Any
) -> None:
    login()
    game.route("GET", "/admin/v1/daily-sets").respond(200, json=[_set(status="locked")])
    game.route("PUT", API_ITEM).respond(409, json=problem(409, "game.daily_locked"))
    response = client.post(
        ITEM_URL,
        {"media_item_id": str(NEW), "reason": "надпись на кадре", "idempotency_key": _key()},
        follow=True,
    )
    page = response.content.decode()
    assert "Набор дня уже заблокирован" in page
    assert 'type="radio"' not in page  # форма замены скрыта: набор только для чтения
    assert "Набор заблокирован" in page
    entry = AuditEntry.objects.get(action="game.replace_daily_item")
    assert entry.error_code == "game.daily_locked"
    assert entry.after is None


def test_replace_unknown_day_is_404(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("GET", "/admin/v1/daily-sets").respond(200, json=[])
    assert client.get(ITEM_URL).status_code == 404
    assert client.get(f"/game/daily/{KEY}/2026-02-30/items/1/").status_code == 404


def test_regenerate_sends_reason_and_audits(client: Any, login: Any, game: Any) -> None:
    login()
    key = _key()
    route = game.route("POST", f"/admin/v1/daily-sets/{KEY}/{DAY.isoformat()}/regenerate").respond(
        200, json=_set()
    )
    response = client.post(
        f"/game/daily/{KEY}/{DAY.isoformat()}/regenerate/",
        {"reason": "пустой день", "idempotency_key": key},
    )
    assert response.status_code == 302
    assert route.calls.last.request.headers["Idempotency-Key"] == key
    assert json.loads(route.calls.last.request.content) == {"reason": "пустой день"}
    assert AuditEntry.objects.get(action="game.regenerate_daily_set").after == {
        "status": "scheduled",
        "mode_version": 2,
        "subject_id": None,
    }


def test_regenerate_without_reason_is_not_sent(client: Any, login: Any, game: Any) -> None:
    login()
    route = game.route("POST", f"/admin/v1/daily-sets/{KEY}/{DAY.isoformat()}/regenerate").respond(
        200, json=_set()
    )
    client.post(f"/game/daily/{KEY}/{DAY.isoformat()}/regenerate/", {"idempotency_key": _key()})
    assert not route.called
    assert not AuditEntry.objects.exists()


def test_moderator_curates_daily_sets(
    client: Any, login: Any, game: Any, moderator_perms: tuple[str, ...]
) -> None:
    login(perms=moderator_perms, roles=("moderator",))
    game.route("GET", "/admin/v1/daily-sets").respond(200, json=[_set()])
    route = game.route("PUT", API_ITEM).respond(200, json=_set(second=NEW))
    client.post(
        ITEM_URL, {"media_item_id": str(NEW), "reason": "надпись", "idempotency_key": _key()}
    )
    assert route.called
