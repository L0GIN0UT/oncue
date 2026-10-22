"""«Режимы», этап 4a: онлайн-шаблоны в редакторе (ADR 0032) и фильтр «соло / онлайн» в списке.

Подсказки строятся по сводной схеме game (``GET /admin/v1/mode-configs/schema``; в заглушке —
контракт ``contracts/modes/mode_config.v1.json``, где ``session`` — ``oneOf`` с ``$ref``).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from admin_service.apps.game_config import config_text

pytestmark = [pytest.mark.component, pytest.mark.django_db]

KEY = "music.duel.classic"
BASE = f"/admin/v1/mode-configs/{KEY}"
NOW = "2026-10-22T09:00:00Z"
ONLINE_BODY: dict[str, Any] = {
    "key": KEY,
    "domain": "music",
    "session": {
        "kind": "duel",
        "players": {"min": 2, "max": 2},
        "rounds": 7,
        "countdown_s": 3,
        "level_windows_s": [20, 30],
        "reveal_s": 5,
        "contest": {"type": "race", "params": {}},
        "disconnect_grace_s": 30,
    },
}


def _detail(body: dict[str, Any], key: str = KEY) -> dict[str, Any]:
    return {
        "key": key,
        "domain": "music",
        "status": "draft",
        "published_version": None,
        "versions": [],
        "draft": {
            "body": body,
            "revision": 1,
            "validation": {"ok": True, "errors": [], "warnings": [], "pool": None},
            "updated_by": str(uuid.uuid4()),
            "updated_at": NOW,
        },
    }


def _summary(key: str, status: str = "published", **kw: Any) -> dict[str, Any]:
    return {
        "key": key,
        "domain": key.split(".", 1)[0],
        "status": status,
        "published_version": 1 if status == "published" else None,
        "has_draft": status == "draft",
        "updated_at": NOW,
    } | kw


def _mode_out(key: str) -> dict[str, Any]:
    return {
        "key": key,
        "version": 1,
        "domain": "music",
        "title_key": f"modes.{key}.title",
        "source_type": "random",
        "clue_type": "audio_intro",
        "rounds": 7,
        "levels": [{"index": 1, "hint": {"seconds": 4}}],
        "allow_guest": False,
        "online": {
            "kind": "duel",
            "players": {"min": 2, "max": 2},
            "rounds": 7,
            "level_windows_s": [20],
            "contest_type": "race",
            "ranked": False,
            "chat": {"emotes": [], "phrases": [], "free_text": False},
        },
    }


# ------------------------------------------------------------------------------ подсказки


def test_editor_shows_online_branch_hints_for_online_draft(
    client: Any, login: Any, game: Any
) -> None:
    login()
    game.route("GET", BASE).respond(200, json=_detail(ONLINE_BODY))
    page = client.get(f"/game/modes/{KEY}/edit/").content.decode()
    assert "Подсказки" in page
    online = page.index("kind = duel | party | lobby")
    assert "текущая ветка" in page[online : online + 200]
    assert "<code>level_windows_s</code>" in page
    assert "<code>disconnect_grace_s</code>" in page
    assert "parallel, race" in page  # плагины contest из сводной схемы
    # Соло-ветка тоже есть, но свёрнута и не помечена текущей.
    solo = page.index("kind = solo")
    assert "текущая ветка" not in page[solo : solo + 80]


def test_editor_keeps_online_session_key_order(client: Any, login: Any, game: Any) -> None:
    login()
    stored = {**ONLINE_BODY, "session": dict(sorted(ONLINE_BODY["session"].items()))}
    game.route("GET", BASE).respond(200, json=_detail(stored))  # jsonb: ключи по алфавиту
    page = client.get(f"/game/modes/{KEY}/edit/").content.decode()
    assert page.index("kind: duel") < page.index("players:") < page.index("countdown_s:")


def test_validate_maps_online_error_path_to_line(client: Any, login: Any, game: Any) -> None:
    login()
    text = config_text.dump(ONLINE_BODY, "yaml")
    game.route("POST", "/admin/v1/mode-configs/validate").respond(
        200,
        json={
            "ok": False,
            "errors": [
                {
                    "path": "session.players.max",
                    "code": "schema.less_than_equal",
                    "message_key": "config.errors.schema.less_than_equal",
                }
            ],
            "warnings": [],
            "pool": None,
        },
    )
    response = client.post(
        f"/game/modes/{KEY}/edit/",
        {"fmt": "yaml", "text": text, "action": "validate", "idempotency_key": str(uuid.uuid4())},
    )
    page = response.content.decode()
    line = next(i for i, row in enumerate(text.splitlines(), 1) if row.strip().startswith("max:"))
    assert f"<tr><td>{line}</td><td><code>session.players.max</code>" in page
    online = page.index("kind = duel | party | lobby")
    assert "текущая ветка" in page[online : online + 200]  # по тексту формы, а не по черновику


def test_editor_works_without_schema(client: Any, login: Any, game: Any, problem: Any) -> None:
    login()
    game.route("GET", "/admin/v1/mode-configs/schema").respond(
        403, json=problem(403, "auth.forbidden")
    )
    game.route("GET", "/admin/v1/mode-configs/music.duel.fresh").respond(
        404, json=problem(404, "game.mode_not_found")
    )
    response = client.get("/game/modes/music.duel.fresh/edit/", {"session": "online"})
    page = response.content.decode()
    assert response.status_code == 200
    assert "Подсказки" not in page
    assert "domain: music" in page
    assert "session:" not in page  # без схемы — только ключ и домен


# ------------------------------------------------------------------------------ новый режим


def test_new_mode_form_passes_session_to_editor(client: Any, login: Any, game: Any) -> None:
    login()
    response = client.get("/game/modes/new/", {"key": "film.party.test", "session": "online"})
    assert response["Location"] == "/game/modes/film.party.test/edit/?session=online"
    response = client.get("/game/modes/new/", {"key": "film.random.test"})
    assert response["Location"] == "/game/modes/film.random.test/edit/"


def test_new_online_mode_starts_from_online_skeleton(
    client: Any, login: Any, game: Any, problem: Any
) -> None:
    login()
    game.route("GET", "/admin/v1/mode-configs/film.party.test").respond(
        404, json=problem(404, "game.mode_not_found")
    )
    page = client.get("/game/modes/film.party.test/edit/", {"session": "online"}).content.decode()
    assert "key: film.party.test" in page
    assert "domain: film" in page
    assert "kind: duel" in page
    assert "level_windows_s:" in page
    assert "disconnect_grace_s:" in page
    assert "allow_guest" not in page.split("<form", 1)[1].split("</form>", 1)[0]
    assert "kind: solo" not in page


def test_new_solo_mode_starts_from_solo_skeleton(
    client: Any, login: Any, game: Any, problem: Any
) -> None:
    login()
    game.route("GET", "/admin/v1/mode-configs/film.random.test").respond(
        404, json=problem(404, "game.mode_not_found")
    )
    page = client.get("/game/modes/film.random.test/edit/").content.decode()
    assert "kind: solo" in page
    assert "allow_guest: false" in page


# ------------------------------------------------------------------------------ список


def test_list_filter_online_uses_published_online_modes(client: Any, login: Any, game: Any) -> None:
    login()
    configs = game.route("GET", "/admin/v1/mode-configs").respond(
        200,
        json=[
            _summary("music.duel.classic"),
            _summary("music.random.classic"),
            _summary("music.party.fresh", status="draft"),
        ],
    )
    online = game.route("GET", "/v1/modes").respond(200, json=[_mode_out("music.duel.classic")])
    page = client.get("/game/modes/", {"session": "online", "domain": "music"}).content.decode()
    assert dict(configs.calls.last.request.url.params) == {"domain": "music"}  # не параметр game
    assert dict(online.calls.last.request.url.params) == {"session": "online"}
    assert 'href="/game/modes/music.duel.classic/"' in page
    assert "music.random.classic" not in page
    assert "music.party.fresh" not in page  # вид сессии неизвестен — не в фильтре
    assert "онлайн" in page


def test_list_filter_solo_keeps_published_non_online(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("GET", "/admin/v1/mode-configs").respond(
        200, json=[_summary("music.duel.classic"), _summary("music.random.classic")]
    )
    game.route("GET", "/v1/modes").respond(200, json=[_mode_out("music.duel.classic")])
    page = client.get("/game/modes/", {"session": "solo"}).content.decode()
    assert 'href="/game/modes/music.random.classic/"' in page
    assert 'href="/game/modes/music.duel.classic/"' not in page


def test_list_filter_prefers_session_kind_from_game(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("GET", "/admin/v1/mode-configs").respond(
        200,
        json=[
            _summary("music.party.fresh", status="draft", session_kind="online"),
            _summary("music.random.classic", session_kind="solo"),
        ],
    )
    online = game.route("GET", "/v1/modes").respond(200, json=[])
    page = client.get("/game/modes/", {"session": "online"}).content.decode()
    assert 'href="/game/modes/music.party.fresh/"' in page
    assert "music.random.classic" not in page
    assert not online.called  # вид известен из строки — публичный список не нужен


def test_list_filter_reports_unavailable_online_list(
    client: Any, login: Any, game: Any, problem: Any
) -> None:
    login()
    game.route("GET", "/admin/v1/mode-configs").respond(200, json=[_summary("music.duel.classic")])
    game.route("GET", "/v1/modes").respond(503, json=problem(503, "service.unavailable", "Нет"))
    response = client.get("/game/modes/", {"session": "online"})
    page = response.content.decode()
    assert response.status_code == 200
    assert 'href="/game/modes/music.duel.classic/"' not in page
    assert "Режимов нет" in page


def test_list_without_session_filter_does_not_call_public_modes(
    client: Any, login: Any, game: Any
) -> None:
    login()
    game.route("GET", "/admin/v1/mode-configs").respond(200, json=[_summary(KEY)])
    online = game.route("GET", "/v1/modes").respond(200, json=[])
    client.get("/game/modes/")
    assert not online.called
