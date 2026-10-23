"""«Прогрессия» (этап 5a, поток P3): конфиг, колесо, задания и бейджи, выдача и сторно, журнал.

Заглушка progression проверяет и ответы (по contracts/openapi/progression.json), и запросы
админки: JSON-тело по схемам запросов и ``Idempotency-Key`` у каждой мутации.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from admin_service.apps.progression.wheel import sector_chances
from admin_service.audit.models import AuditEntry

pytestmark = [pytest.mark.component, pytest.mark.django_db]

USER = uuid.uuid4()
PLAYER = uuid.uuid4()
NOW = "2026-10-23T10:00:00Z"
SHA = "a" * 64
CONFIGS = "/admin/v1/configs"
LEDGER = f"/admin/v1/users/{PLAYER}/ledger"
MANAGE_ONLY = ("admin.access", "progression.config.manage")
READ_ONLY = ("admin.access", "progression.ledger.read")
CONFIG = {"timezone": "Europe/Moscow", "levels": {"base": 500, "step": 20, "max_level": 100}}
WHEEL = {
    "wheel": {
        "enabled": True,
        "sectors": [
            {
                "key": "xp_100",
                "title": "100 опыта",
                "weight": 3,
                "prize": {"kind": "xp", "amount": 100},
            },
            {
                "key": "verses_5",
                "title": "5 Версов",
                "weight": 1,
                "prize": {"kind": "verses", "amount": 5},
            },
        ],
    }
}


def _key() -> str:
    return str(uuid.uuid4())


def _card(version: int, status: str) -> dict[str, Any]:
    return {
        "version": version,
        "status": status,
        "created_at": NOW,
        "created_by": str(USER),
        "body_sha256": SHA,
        "published_at": NOW if status != "draft" else None,
    }


def _entry(op_id: str = "solo:1", **patch: Any) -> dict[str, Any]:
    return {
        "op_id": op_id,
        "direction": "credit",
        "amount": 3,
        "bucket": "earned",
        "reason": "solo_round",
        "source_ref": "s1",
        "reversal_of": None,
        "actor_id": None,
        "config_version": 1,
        "at": NOW,
    } | patch


def _stub_versions(progression: Any, *, draft: bool = True) -> None:
    items = [_card(1, "published")]
    if draft:
        items.insert(0, _card(2, "draft"))
    progression.route("GET", CONFIGS).respond(200, json={"items": items})


def _version(progression: Any, number: int, status: str, config: dict[str, Any]) -> None:
    progression.route("GET", f"{CONFIGS}/{number}").respond(
        200, json={"card": _card(number, status), "config": config}
    )


# ------------------------------------------------------------------------------ доступ


def test_section_requires_manage_perm(client: Any, login: Any, moderator_perms: Any) -> None:
    login(perms=moderator_perms, roles=("moderator",))
    for path in ("", "edit/", "wheel/", "quests/"):
        assert client.get(f"/progression/{path}").status_code == 403
    body = {"reason": "тест прав", "idempotency_key": _key()}
    assert client.post("/progression/publish/", body | {"version": 2}).status_code == 403
    assert client.post("/progression/rollback/", body | {"to_version": 1}).status_code == 403
    assert 'href="/progression/"' not in client.get("/").content.decode()
    assert not AuditEntry.objects.exists()


def test_menu_shows_section_with_perms(client: Any, login: Any) -> None:
    login()
    page = client.get("/").content.decode()
    assert 'href="/progression/"' in page
    assert 'href="/progression/ledger/"' in page


def test_ledger_actions_need_their_own_perms(client: Any, login: Any) -> None:
    login(perms=MANAGE_ONLY, roles=("moderator",))
    assert client.get("/progression/ledger/").status_code == 403
    assert client.get(f"/progression/ledger/{PLAYER}/").status_code == 403
    body = {"reason": "достаточно длинная причина", "idempotency_key": _key()}
    grant = client.post(f"/progression/ledger/{PLAYER}/grant/", body | {"amount": 5})
    assert grant.status_code == 403
    reverse = client.post(f"/progression/ledger/{PLAYER}/reverse/", body | {"op_id": "x"})
    assert reverse.status_code == 403
    assert not AuditEntry.objects.exists()


def test_grant_and_reverse_need_separate_perms(client: Any, login: Any) -> None:
    login(perms=READ_ONLY, roles=("moderator",))
    body = {"reason": "достаточно длинная причина", "idempotency_key": _key()}
    assert (
        client.post(f"/progression/ledger/{PLAYER}/grant/", body | {"amount": 5}).status_code == 403
    )
    assert (
        client.post(f"/progression/ledger/{PLAYER}/reverse/", body | {"op_id": "x"}).status_code
        == 403
    )


def test_publish_needs_publish_perm(client: Any, login: Any, progression: Any) -> None:
    login(perms=MANAGE_ONLY, roles=("moderator",))
    response = client.post(
        "/progression/publish/",
        {"version": 2, "body_sha256": SHA, "reason": "релиз", "idempotency_key": _key()},
    )
    assert response.status_code == 403


def test_publish_button_hidden_without_publish_perm(
    client: Any, login: Any, progression: Any
) -> None:
    login(perms=MANAGE_ONLY, roles=("moderator",))
    _stub_versions(progression)
    _version(progression, 1, "published", CONFIG)
    _version(progression, 2, "draft", CONFIG)
    page = client.get("/progression/").content.decode()
    assert "Опубликовать v2" not in page
    assert "Скопировать в черновик" not in page


# ------------------------------------------------------------------------------ конфиг


def test_configs_show_versions_and_draft_diff(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_versions(progression)
    _version(progression, 1, "published", CONFIG)
    _version(
        progression, 2, "draft", CONFIG | {"levels": {"base": 900, "step": 20, "max_level": 100}}
    )
    page = client.get("/progression/").content.decode()
    assert "Отличия v1 → v2" in page
    assert "base: 900" in page
    assert "Опубликовать v2" in page


def test_configs_survive_service_error(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    progression.route("GET", CONFIGS).respond(403, json=problem(403, "auth.forbidden"))
    response = client.get("/progression/")
    assert response.status_code == 200
    assert "Недостаточно прав" in response.content.decode()


def test_editor_saves_draft_with_idempotency_key(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_versions(progression, draft=False)
    _version(progression, 1, "published", CONFIG)
    page = client.get("/progression/edit/").content.decode()
    assert "base: 500" in page

    key = _key()
    route = progression.route("PUT", f"{CONFIGS}/draft").respond(
        200, json={"card": _card(2, "draft"), "config": CONFIG}
    )
    text = "timezone: Europe/Moscow\nlevels:\n  base: 600\n  step: 20\n  max_level: 100\n"
    response = client.post(
        "/progression/edit/",
        {"fmt": "yaml", "text": text, "reason": "меняем кривую", "idempotency_key": key},
    )
    assert response.status_code == 302
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert json.loads(request.content)["config"]["levels"]["base"] == 600
    entry = AuditEntry.objects.get(action="progression.save_draft")
    assert (entry.service, entry.target_type, entry.target_id) == ("progression", "config", "draft")
    assert entry.after == {"card": _card(2, "draft")}  # тело конфига в аудит не пишется


def test_editor_shows_validation_errors_with_lines(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    text = "timezone: Europe/Moscow\nlevels:\n  base: много\n"
    progression.route("PUT", f"{CONFIGS}/draft").respond_off_contract(
        422,
        json=problem(422, "progression.config_invalid")
        | {"details": {"errors": [{"path": "levels.base", "reason": "ожидается целое"}]}},
    )
    response = client.post(
        "/progression/edit/",
        {"fmt": "yaml", "text": text, "reason": "правка", "idempotency_key": _key()},
    )
    page = response.content.decode()
    assert "levels.base" in page
    assert "ожидается целое" in page
    assert "много" in page  # текст остаётся в форме
    entry = AuditEntry.objects.get(action="progression.save_draft")
    assert entry.outcome == AuditEntry.Outcome.FAILED
    assert entry.error_code == "progression.config_invalid"


def test_editor_rejects_broken_text_without_service_call(client: Any, login: Any) -> None:
    login()
    broken = client.post(
        "/progression/edit/",
        {"fmt": "yaml", "text": "a: [", "reason": "правка", "idempotency_key": _key()},
    )
    assert broken.status_code == 400
    assert not AuditEntry.objects.exists()


def test_publish_sends_version_and_audits(client: Any, login: Any, progression: Any) -> None:
    login()
    key = _key()
    route = progression.route("POST", f"{CONFIGS}/draft/publish").respond(
        200, json={"card": _card(2, "published"), "config": CONFIG}
    )
    response = client.post(
        "/progression/publish/",
        {"version": 2, "body_sha256": SHA, "reason": "релиз", "idempotency_key": key},
    )
    assert response["Location"] == "/progression/"
    assert route.calls.last.request.headers["Idempotency-Key"] == key
    assert json.loads(route.calls.last.request.content) == {
        "version": 2,
        "body_sha256": SHA,
        "reason": "релиз",
    }
    entry = AuditEntry.objects.get(action="progression.publish_config")
    assert entry.before == {"draft_version": 2}


def test_publish_conflict_is_shown_and_audited(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    progression.route("POST", f"{CONFIGS}/draft/publish").respond(
        409, json=problem(409, "progression.config_not_draft", "Версия не черновик")
    )
    _stub_versions(progression, draft=False)
    _version(progression, 1, "published", CONFIG)
    response = client.post(
        "/progression/publish/",
        {"version": 1, "body_sha256": SHA, "reason": "релиз", "idempotency_key": _key()},
        follow=True,
    )
    assert "Версия не черновик" in response.content.decode()
    entry = AuditEntry.objects.get(action="progression.publish_config")
    assert entry.error_code == "progression.config_not_draft"


def test_rollback_posts_to_version(client: Any, login: Any, progression: Any) -> None:
    login()
    route = progression.route("POST", f"{CONFIGS}/1/rollback").respond(
        201, json={"card": _card(3, "draft"), "config": CONFIG}
    )
    key = _key()
    client.post(
        "/progression/rollback/", {"to_version": 1, "reason": "вернуть", "idempotency_key": key}
    )
    assert route.calls.last.request.headers["Idempotency-Key"] == key
    assert json.loads(route.calls.last.request.content) == {"reason": "вернуть"}
    assert AuditEntry.objects.filter(action="progression.rollback_config").exists()


# ------------------------------------------------------------------------------ колесо


def test_sector_chances_are_weight_over_total() -> None:
    rows = sector_chances(WHEEL)
    assert [(r.key, r.weight, r.chance) for r in rows] == [
        ("xp_100", 3, 0.75),
        ("verses_5", 1, 0.25),
    ]
    assert sector_chances({"wheel": {"sectors": [{"weight": 0}, {"weight": "x"}]}}) == []
    assert sector_chances(None) == []


def test_wheel_preview_is_computed_from_config(client: Any, login: Any, progression: Any) -> None:
    login()
    _stub_versions(progression)
    _version(progression, 2, "draft", CONFIG | WHEEL)
    page = client.get("/progression/wheel/").content.decode()
    assert "75%" in page
    assert "25%" in page
    assert "100 опыта" in page


# ------------------------------------------------------------------------------ задания, бейджи


def test_quests_and_badges_page(client: Any, login: Any, progression: Any) -> None:
    login()
    progression.route("GET", "/admin/v1/quests").respond(
        200,
        json={
            "config_version": 1,
            "items": [
                {
                    "key": "play_3_solo",
                    "period": "daily",
                    "title": "Сыграть 3 раунда",
                    "reward": {"xp": 30, "verses": 5},
                    "enabled": True,
                    "completed_total": 7,
                }
            ],
        },
    )
    progression.route("GET", "/admin/v1/badges").respond(
        200,
        json={
            "config_version": 1,
            "items": [
                {"key": "first_win", "title": "Первая победа", "enabled": True, "unlocked_total": 4}
            ],
        },
    )
    page = client.get("/progression/quests/").content.decode()
    assert "Сыграть 3 раунда" in page
    assert "Первая победа" in page


# ------------------------------------------------------------------------------ журнал


def _ledger_page(items: list[dict[str, Any]], cursor: str | None = None) -> dict[str, Any]:
    return {
        "user_id": str(PLAYER),
        "earned": 10,
        "paid": 0,
        "items": items,
        "next_cursor": cursor,
    }


def test_ledger_search_redirects_to_user(client: Any, login: Any) -> None:
    login()
    response = client.get("/progression/ledger/", {"user_id": str(PLAYER)})
    assert response["Location"] == f"/progression/ledger/{PLAYER}/"
    assert client.get("/progression/ledger/", {"user_id": "nope"}).status_code == 400


def test_ledger_page_shows_entries_cursor_and_reverse_forms(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    route = progression.route("GET", LEDGER).respond(
        200,
        json=_ledger_page(
            [_entry("solo:1"), _entry("admin:rev", direction="debit", reversal_of="solo:0")],
            cursor="abc_DEF-1",
        ),
    )
    page = client.get(f"/progression/ledger/{PLAYER}/", {"cursor": "xyz"}).content.decode()
    assert route.calls.last.request.url.params["cursor"] == "xyz"
    assert "solo:1" in page
    assert "?cursor=abc_DEF-1" in page
    assert page.count('value="Сторнировать"') == 1  # у проводки-сторно кнопки нет
    assert "Выдать Версы" in page


def test_ledger_buttons_follow_perms(client: Any, login: Any, progression: Any) -> None:
    login(perms=READ_ONLY, roles=("moderator",))
    progression.route("GET", LEDGER).respond(200, json=_ledger_page([_entry()]))
    page = client.get(f"/progression/ledger/{PLAYER}/").content.decode()
    assert "Выдать Версы" not in page
    assert 'value="Сторнировать"' not in page


def test_ledger_unknown_player_is_404(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    progression.route("GET", LEDGER).respond(404, json=problem(404, "progression.user_not_found"))
    assert client.get(f"/progression/ledger/{PLAYER}/").status_code == 404


def test_grant_sends_amount_reason_and_key(client: Any, login: Any, progression: Any) -> None:
    login()
    key = _key()
    route = progression.route("POST", f"{LEDGER}/grant").respond(
        201,
        json={
            "user_id": str(PLAYER),
            "entry": _entry("admin:k", actor_id=str(USER), reason="admin_grant"),
            "balance_after": 15,
        },
    )
    response = client.post(
        f"/progression/ledger/{PLAYER}/grant/",
        {"amount": 5, "reason": "компенсация за сбой", "idempotency_key": key},
    )
    assert response["Location"] == f"/progression/ledger/{PLAYER}/"
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert json.loads(request.content) == {
        "amount": 5,
        "reason": "компенсация за сбой",
    }
    entry = AuditEntry.objects.get(action="progression.grant")
    assert entry.target_id == str(PLAYER)
    assert entry.after == {"balance_after": 15}


def test_grant_requires_reason_and_positive_amount(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    progression.route("GET", LEDGER).respond(200, json=_ledger_page([]))
    for bad in ({"amount": 5, "reason": ""}, {"amount": 0, "reason": "достаточно длинная"}):
        response = client.post(
            f"/progression/ledger/{PLAYER}/grant/",
            bad | {"idempotency_key": _key()},
        )
        assert response.status_code == 400
    assert not AuditEntry.objects.exists()


def test_grant_limit_error_is_shown_and_audited(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    progression.route("POST", f"{LEDGER}/grant").respond(
        422, json=problem(422, "progression.grant_limit_exceeded", "Лимит выдачи превышен")
    )
    progression.route("GET", LEDGER).respond(200, json=_ledger_page([]))
    response = client.post(
        f"/progression/ledger/{PLAYER}/grant/",
        {
            "amount": 999999,
            "reason": "достаточно длинная",
            "idempotency_key": _key(),
        },
        follow=True,
    )
    assert "Лимит выдачи превышен" in response.content.decode()
    entry = AuditEntry.objects.get(action="progression.grant")
    assert entry.error_code == "progression.grant_limit_exceeded"


def test_reverse_posts_op_id_with_reason(client: Any, login: Any, progression: Any) -> None:
    login()
    key = _key()
    route = progression.route("POST", "/admin/v1/ledger/solo%3A1/reverse").respond(
        201,
        json={
            "user_id": str(PLAYER),
            "entry": _entry("admin:r", direction="debit", reversal_of="solo:1"),
            "balance_after": 7,
        },
    )
    client.post(
        f"/progression/ledger/{PLAYER}/reverse/",
        {"op_id": "solo:1", "reason": "ошибочное начисление", "idempotency_key": key},
    )
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert json.loads(request.content) == {"reason": "ошибочное начисление"}
    entry = AuditEntry.objects.get(action="progression.reverse")
    assert (entry.target_type, entry.target_id) == ("ledger", "solo:1")


def test_reverse_conflict_is_audited(
    client: Any, login: Any, progression: Any, problem: Any
) -> None:
    login()
    progression.route("POST", "/admin/v1/ledger/solo%3A1/reverse").respond(
        409,
        json=problem(409, "progression.insufficient_balance", "Недостаточно средств на балансе"),
    )
    progression.route("GET", LEDGER).respond(200, json=_ledger_page([]))
    response = client.post(
        f"/progression/ledger/{PLAYER}/reverse/",
        {"op_id": "solo:1", "reason": "ошибочное начисление", "idempotency_key": _key()},
        follow=True,
    )
    assert "Недостаточно средств" in response.content.decode()
    entry = AuditEntry.objects.get(action="progression.reverse")
    assert entry.error_code == "progression.insufficient_balance"
