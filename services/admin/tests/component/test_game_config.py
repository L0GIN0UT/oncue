"""Раздел «Режимы»: редактор, проверка, публикация, откат и архив через admin-API game.

Заглушка game проверяет и ответы (по contracts/openapi/game.json), и запросы админки:
JSON-тело по схеме и ``Idempotency-Key`` у мутаций.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from admin_service.apps.game_config import config_text
from admin_service.audit.models import AuditEntry
from django.test import override_settings

pytestmark = [pytest.mark.component, pytest.mark.django_db]

KEY = "music.random.classic"
BASE = f"/admin/v1/mode-configs/{KEY}"
NOW = "2026-10-10T09:00:00Z"
PUBLISHED_BODY: dict[str, Any] = {
    "key": KEY,
    "domain": "music",
    "scoring": {"type": "by_level", "params": {"points": [6, 5, 4, 3, 2, 1]}},
    "session": {"kind": "solo", "rounds": 5},
}
DRAFT_BODY: dict[str, Any] = {
    **PUBLISHED_BODY,
    "scoring": {"type": "by_level", "params": {"points": [8, 6, 4, 3, 2, 1]}},
}


def _validation(*, ok: bool = True, errors: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {"ok": ok, "errors": errors or [], "warnings": [], "pool": {"playable_count": 120}}


def _draft(
    revision: int = 3, body: dict[str, Any] | None = None, *, ok: bool = True
) -> dict[str, Any]:
    return {
        "body": body or DRAFT_BODY,
        "revision": revision,
        "validation": _validation(ok=ok),
        "updated_by": str(uuid.uuid4()),
        "updated_at": NOW,
    }


def _version(version: int = 1, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "version": version,
        "status": "published",
        "body_hash": f"sha256:{version:064d}",
        "actor_id": None,
        "reason": "сид",
        "created_at": NOW,
        "rolled_back_from": None,
        "key": KEY,
        "body": body or PUBLISHED_BODY,
    }


def _summary(version: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in version.items() if k not in ("key", "body")}


def _detail(*, draft: dict[str, Any] | None = None, versions: int = 1) -> dict[str, Any]:
    return {
        "key": KEY,
        "domain": "music",
        "status": "published",
        "published_version": versions,
        "versions": [_summary(_version(v)) for v in range(1, versions + 1)],
        "draft": draft,
    }


def _key() -> str:
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _no_asset_requests(game: Any) -> None:
    """Карточка режима читает запросы ассетов (этап 3a); по умолчанию их нет."""
    game.route("GET", f"{BASE}/asset-requests").respond(200, json=[])


def _body(route: Any) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(route.calls.last.request.content)
    return data


# ------------------------------------------------------------------------------ чтение


def test_list_shows_modes_with_links(client: Any, login: Any, game: Any) -> None:
    login()
    route = game.route("GET", "/admin/v1/mode-configs").respond(
        200,
        json=[
            {
                "key": KEY,
                "domain": "music",
                "status": "published",
                "published_version": 2,
                "has_draft": True,
                "updated_at": NOW,
            }
        ],
    )
    page = client.get("/game/modes/", {"domain": "music"}).content.decode()
    assert f'href="/game/modes/{KEY}/"' in page
    assert "v2" in page
    assert dict(route.calls.last.request.url.params) == {"domain": "music"}


def test_detail_shows_versions_and_diff_with_published(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("GET", BASE).respond(200, json=_detail(draft=_draft(), versions=2))
    game.route("GET", f"{BASE}/versions/2").respond(200, json=_version(2))
    page = client.get(f"/game/modes/{KEY}/").content.decode()
    assert "-    - 5" in page  # строка опубликованной версии [6, 5, ...]
    assert "+    - 8" in page  # строка черновика [8, 6, ...]
    assert f'action="/game/modes/{KEY}/publish/"' in page
    assert "Опубликовать r3" in page


def test_detail_unknown_mode_offers_to_create(
    client: Any, login: Any, game: Any, problem: Any
) -> None:
    login()
    game.route("GET", BASE).respond(404, json=problem(404, "game.mode_not_found"))
    response = client.get(f"/game/modes/{KEY}/")
    assert response.status_code == 404
    assert f'href="/game/modes/{KEY}/edit/"' in response.content.decode()


def test_invalid_draft_cannot_be_published_from_ui(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("GET", BASE).respond(200, json=_detail(draft=_draft(ok=False)))
    game.route("GET", f"{BASE}/versions/1").respond(200, json=_version(1))
    page = client.get(f"/game/modes/{KEY}/").content.decode()
    assert "не прошёл проверку" in page
    assert "/publish/" not in page


def test_moderator_sees_configs_read_only(
    client: Any, login: Any, game: Any, moderator_perms: tuple[str, ...]
) -> None:
    login(perms=moderator_perms, roles=("moderator",))
    game.route("GET", BASE).respond(200, json=_detail(draft=_draft()))
    game.route("GET", f"{BASE}/versions/1").respond(200, json=_version(1))
    page = client.get(f"/game/modes/{KEY}/").content.decode()
    assert "/publish/" not in page
    assert "/edit/" not in page
    assert "/rollback/" not in page


# ------------------------------------------------------------------------------ редактор


def test_editor_prefills_draft(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("GET", BASE).respond(200, json=_detail(draft=_draft(revision=7)))
    page = client.get(f"/game/modes/{KEY}/edit/").content.decode()
    assert "music.random.classic" in page
    assert 'name="base_revision" value="7"' in page


def test_editor_for_new_mode_starts_from_key(
    client: Any, login: Any, game: Any, problem: Any
) -> None:
    login()
    game.route("GET", "/admin/v1/mode-configs/film.daily.frame").respond(
        404, json=problem(404, "game.mode_not_found")
    )
    page = client.get("/game/modes/film.daily.frame/edit/").content.decode()
    assert "domain: film" in page
    assert "ещё нет" in page


def test_validate_shows_errors_with_paths_and_lines(client: Any, login: Any, game: Any) -> None:
    login()
    text = "key: music.random.classic\ndomain: music\nsession:\n  rounds: 500\n"
    route = game.route("POST", "/admin/v1/mode-configs/validate").respond(
        200,
        json=_validation(
            ok=False,
            errors=[
                {
                    "path": "session.rounds",
                    "code": "schema.maximum",
                    "message_key": "config.errors.maximum",
                }
            ],
        ),
    )
    response = client.post(
        f"/game/modes/{KEY}/edit/",
        {"action": "validate", "fmt": "yaml", "text": text, "idempotency_key": _key()},
    )
    page = response.content.decode()
    assert response.status_code == 200
    assert "session.rounds" in page
    assert "schema.maximum" in page
    assert "<td>4</td><td><code>session.rounds" in page  # строка 4 текста
    assert _body(route) == {"body": config_text.parse(text, "yaml")}
    assert "Idempotency-Key" not in route.calls.last.request.headers


def test_syntax_error_is_shown_without_calling_game(client: Any, login: Any, game: Any) -> None:
    login()
    route = game.route("POST", "/admin/v1/mode-configs/validate").respond(200, json=_validation())
    response = client.post(
        f"/game/modes/{KEY}/edit/",
        {"action": "validate", "fmt": "yaml", "text": "a: 1\na: 2\n", "idempotency_key": _key()},
    )
    assert response.status_code == 400
    assert "строка 2" in response.content.decode()
    assert not route.called


@override_settings(GV_MODE_CONFIG_MAX_BYTES=1024)
def test_too_large_config_is_rejected(client: Any, login: Any, game: Any) -> None:
    login()
    response = client.post(
        f"/game/modes/{KEY}/edit/",
        {"action": "save", "fmt": "yaml", "text": "a: " + "x" * 2000, "idempotency_key": _key()},
    )
    assert response.status_code == 400
    assert "больше 1024 байт" in response.content.decode()


def test_save_sends_key_reason_and_base_revision_and_audits(
    client: Any, login: Any, game: Any
) -> None:
    login()
    key = _key()
    route = game.route("PUT", f"{BASE}/draft").respond(200, json=_draft(revision=4))
    text = json.dumps(DRAFT_BODY)
    response = client.post(
        f"/game/modes/{KEY}/edit/",
        {
            "action": "save",
            "fmt": "json",
            "text": text,
            "base_revision": "3",
            "reason": "поднять очки за первый уровень",
            "idempotency_key": key,
        },
    )
    assert response.status_code == 302
    assert route.calls.last.request.headers["Idempotency-Key"] == key
    assert _body(route) == {
        "body": DRAFT_BODY,
        "base_revision": 3,
        "reason": "поднять очки за первый уровень",
    }
    entry = AuditEntry.objects.get(action="game.save_mode_draft")
    assert (entry.target_id, entry.error_code) == (KEY, "")
    assert entry.before == {"revision": 3}
    assert entry.after == {"revision": 4}
    assert entry.idempotency_key == key


def test_save_requires_reason(client: Any, login: Any, game: Any) -> None:
    login()
    route = game.route("PUT", f"{BASE}/draft").respond(200, json=_draft())
    response = client.post(
        f"/game/modes/{KEY}/edit/",
        {"action": "save", "fmt": "yaml", "text": "key: x\n", "idempotency_key": _key()},
    )
    assert response.status_code == 400
    assert "укажите причину" in response.content.decode()
    assert not route.called


def test_save_conflict_keeps_text_shows_diff_and_audits_refusal(
    client: Any, login: Any, game: Any, problem: Any
) -> None:
    login()
    game.route("PUT", f"{BASE}/draft").respond(409, json=problem(409, "game.draft_conflict"))
    game.route("GET", BASE).respond(200, json=_detail(draft=_draft(revision=5)))
    mine = {**DRAFT_BODY, "session": {"kind": "solo", "rounds": 7}}
    response = client.post(
        f"/game/modes/{KEY}/edit/",
        {
            "action": "save",
            "fmt": "json",
            "text": json.dumps(mine),
            "base_revision": "3",
            "reason": "больше раундов",
            "idempotency_key": _key(),
        },
    )
    page = response.content.decode()
    assert "изменил кто-то другой" in page
    assert "+  rounds: 7" in page  # diff «текущий черновик → ваш текст»
    assert "&quot;rounds&quot;: 7" in page  # текст остался в поле
    entry = AuditEntry.objects.get(action="game.save_mode_draft")
    assert entry.error_code == "game.draft_conflict"


# ------------------------------------------------------------------------------ публикация


def test_publish_sends_idempotency_key_and_reason(client: Any, login: Any, game: Any) -> None:
    login()
    key = _key()
    game.route("GET", BASE).respond(200, json=_detail(draft=_draft(revision=3)))
    route = game.route("POST", f"{BASE}/publish").respond(200, json=_version(2, DRAFT_BODY))
    response = client.post(
        f"/game/modes/{KEY}/publish/",
        {"draft_revision": "3", "reason": "новые очки", "idempotency_key": key},
    )
    assert response.status_code == 302
    assert route.calls.last.request.headers["Idempotency-Key"] == key
    assert _body(route) == {"draft_revision": 3, "reason": "новые очки"}
    entry = AuditEntry.objects.get(action="game.publish_mode")
    assert entry.before == {"status": "published", "published_version": 1, "draft_revision": 3}
    assert entry.after is not None
    assert entry.after["version"] == 2


def test_publish_refusal_is_shown_and_audited(
    client: Any, login: Any, game: Any, problem: Any
) -> None:
    login()
    game.route("GET", BASE).respond(200, json=_detail(draft=_draft(revision=3)))
    game.route("POST", f"{BASE}/publish").respond(409, json=problem(409, "game.config_invalid"))
    game.route("GET", f"{BASE}/versions/1").respond(200, json=_version(1))
    response = client.post(
        f"/game/modes/{KEY}/publish/",
        {"draft_revision": "3", "reason": "новые очки", "idempotency_key": _key()},
        follow=True,
    )
    assert "не прошёл проверку" in response.content.decode()
    entry = AuditEntry.objects.get(action="game.publish_mode")
    assert entry.error_code == "game.config_invalid"


def test_rollback_and_archive(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("GET", BASE).respond(200, json=_detail(versions=3))
    rollback = game.route("POST", f"{BASE}/rollback").respond(
        200, json={**_version(4), "rolled_back_from": 1}
    )
    archive = game.route("POST", f"{BASE}/archive").respond(
        200,
        json={
            "key": KEY,
            "domain": "music",
            "status": "archived",
            "published_version": 4,
            "has_draft": False,
            "updated_at": NOW,
        },
    )
    client.post(
        f"/game/modes/{KEY}/rollback/",
        {"to_version": "1", "reason": "откат очков", "idempotency_key": _key()},
    )
    client.post(
        f"/game/modes/{KEY}/archive/", {"reason": "режим закрыт", "idempotency_key": _key()}
    )
    assert _body(rollback) == {"to_version": 1, "reason": "откат очков"}
    assert _body(archive) == {"reason": "режим закрыт"}
    assert AuditEntry.objects.get(action="game.rollback_mode").after["rolled_back_from"] == 1
    assert AuditEntry.objects.get(action="game.archive_mode").after["status"] == "archived"


def test_publish_without_reason_is_not_sent(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("GET", BASE).respond(200, json=_detail(draft=_draft()))
    game.route("GET", f"{BASE}/versions/1").respond(200, json=_version(1))
    route = game.route("POST", f"{BASE}/publish").respond(200, json=_version(2))
    response = client.post(
        f"/game/modes/{KEY}/publish/", {"draft_revision": "3", "idempotency_key": _key()}
    )
    assert response.status_code == 400
    assert not route.called


# ------------------------------------------------------------------------------ права


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", f"/game/modes/{KEY}/publish/"),
        ("post", f"/game/modes/{KEY}/rollback/"),
        ("post", f"/game/modes/{KEY}/archive/"),
        ("get", f"/game/modes/{KEY}/edit/"),
        ("post", f"/game/modes/{KEY}/edit/"),
    ],
)
def test_moderator_cannot_edit_or_publish(  # noqa: PLR0917 — фикстуры и параметры pytest
    client: Any, login: Any, game: Any, moderator_perms: tuple[str, ...], method: str, path: str
) -> None:
    login(perms=moderator_perms, roles=("moderator",))
    publish = game.route("POST", f"{BASE}/publish").respond(200, json=_version(2))
    response = getattr(client, method)(path, {"reason": "xxx", "idempotency_key": _key()})
    assert response.status_code == 403
    assert not publish.called
    assert not AuditEntry.objects.exists()


def test_without_view_perm_section_is_hidden_and_forbidden(client: Any, login: Any) -> None:
    login(perms=("admin.access",), roles=("support",))
    assert client.get("/game/modes/").status_code == 403
    index = client.get("/").content.decode()
    assert "/game/modes/" not in index
    assert "/game/daily/" not in index
    assert "/catalog/" in index  # раздел без отдельного права виден всем с admin.access


def test_menu_shows_game_sections_with_perms(client: Any, login: Any) -> None:
    login()
    index = client.get("/").content.decode()
    assert 'href="/game/modes/"' in index
    assert 'href="/game/daily/"' in index


def test_unknown_mode_key_format_is_404(client: Any, login: Any, game: Any) -> None:
    login()
    game.route("GET", "/admin/v1/mode-configs").respond(200, json=[])
    assert client.get("/game/modes/Music.Daily.X/").status_code == 404
    response = client.get("/game/modes/new/", {"key": "bad key"}, follow=True)
    assert "music|film" in response.content.decode()
