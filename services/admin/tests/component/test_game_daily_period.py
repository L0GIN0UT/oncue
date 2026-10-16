"""«Наборы дня», этап 3a: наборы недели (сетка), тема периода и её смена через regenerate."""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

import pytest
from admin_service.audit.models import AuditEntry

pytestmark = [pytest.mark.component, pytest.mark.django_db]

KEY = "film.bracket.weekly"
WEEK = date(2026, 10, 19)  # начало периода
THEME, NEW_THEME = uuid.uuid4(), uuid.uuid4()
REGENERATE = f"/admin/v1/daily-sets/{KEY}/{WEEK.isoformat()}/regenerate"
REGENERATE_URL = f"/game/daily/{KEY}/{WEEK.isoformat()}/regenerate/"


def _week(status: str = "scheduled", subject: uuid.UUID | None = THEME) -> dict[str, Any]:
    return {
        "mode_key": KEY,
        "day": WEEK.isoformat(),
        "mode_version": 1,
        "status": status,
        "period": "week",
        "subject_id": str(subject) if subject else None,
        "items": [
            {
                "index": i,
                "media_item_id": str(uuid.uuid4()),
                "title": f"Фильм {i}",
                "year": 2000 + i,
                "playable": True,
            }
            for i in range(2)
        ],
    }


def _key() -> str:
    return str(uuid.uuid4())


def test_calendar_shows_week_period_and_theme(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("GET", "/admin/v1/mode-configs").respond(200, json=[])
    game.route("GET", "/admin/v1/daily-sets").respond(200, json=[_week()])
    page = client.get("/game/daily/", {"mode_key": KEY, "start": WEEK.isoformat()}).content.decode()
    assert f"неделя с {WEEK.isoformat()}" in page
    assert f"<code>{THEME}</code>" in page
    assert 'name="subject_id"' in page  # смена темы
    assert f"/game/daily/{KEY}/{WEEK.isoformat()}/items/1/" in page  # замена единицы


def test_daily_set_has_no_theme_field(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("GET", "/admin/v1/mode-configs").respond(200, json=[])
    day = {**_week(subject=None), "period": "day", "mode_key": "film.daily.frame"}
    game.route("GET", "/admin/v1/daily-sets").respond(200, json=[day])
    page = client.get("/game/daily/", {"mode_key": "film.daily.frame"}).content.decode()
    assert "неделя" not in page
    assert 'name="subject_id"' not in page
    assert "Перегенерировать набор" in page


def test_change_theme_sends_subject_and_audits_old_and_new(
    client: Any, login: Any, game: Any
) -> None:
    login()
    key = _key()
    game.route("GET", "/admin/v1/daily-sets").respond(200, json=[_week()])
    route = game.route("POST", REGENERATE).respond(200, json=_week(subject=NEW_THEME))
    response = client.post(
        REGENERATE_URL,
        {"reason": "тема недели", "subject_id": str(NEW_THEME), "idempotency_key": key},
    )
    assert response.status_code == 302
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert json.loads(request.content) == {"reason": "тема недели", "subject_id": str(NEW_THEME)}
    entry = AuditEntry.objects.get(action="game.change_daily_theme")
    assert entry.target_id == f"{KEY}/{WEEK.isoformat()}"
    assert entry.before == {"subject_id": str(THEME)}
    assert entry.after == {"status": "scheduled", "mode_version": 1, "subject_id": str(NEW_THEME)}


def test_unplayable_theme_is_explained_and_audited(
    client: Any, login: Any, game: Any, problem: Any
) -> None:
    login()
    game.route("GET", "/admin/v1/daily-sets").respond(200, json=[_week()])
    game.route("GET", "/admin/v1/mode-configs").respond(200, json=[])
    game.route("POST", REGENERATE).respond(409, json=problem(409, "game.theme_not_playable"))
    response = client.post(
        REGENERATE_URL,
        {"reason": "тема недели", "subject_id": str(NEW_THEME), "idempotency_key": _key()},
        follow=True,
    )
    assert "не хватает играбельных единиц" in response.content.decode()
    entry = AuditEntry.objects.get(action="game.change_daily_theme")
    assert entry.error_code == "game.theme_not_playable"


def test_bad_subject_id_is_not_sent(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("GET", "/admin/v1/mode-configs").respond(200, json=[])
    game.route("GET", "/admin/v1/daily-sets").respond(200, json=[_week()])
    route = game.route("POST", REGENERATE).respond(200, json=_week())
    response = client.post(
        REGENERATE_URL,
        {"reason": "тема", "subject_id": "не-uuid", "idempotency_key": _key()},
        follow=True,
    )
    assert "Перегенерация не отправлена" in response.content.decode()
    assert not route.called
    assert not AuditEntry.objects.exists()


def test_viewer_cannot_change_theme(client: Any, login: Any, game: Any) -> None:
    login(perms=("admin.access", "game.daily.view"), roles=("support",))
    game.route("GET", "/admin/v1/daily-sets").respond(200, json=[_week()])
    route = game.route("POST", REGENERATE).respond(200, json=_week())
    page = client.get("/game/daily/", {"mode_key": KEY}).content.decode()
    assert 'name="subject_id"' not in page
    response = client.post(
        REGENERATE_URL, {"reason": "тема", "subject_id": str(NEW_THEME), "idempotency_key": _key()}
    )
    assert response.status_code == 403
    assert not route.called
