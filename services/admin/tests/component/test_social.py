"""«Модерация» (этап 5c, поток F4): очередь, кейс, игрок и санкции, конфиг social.

Заглушка social проверяет и ответы (по contracts/openapi/social.json), и запросы админки:
JSON-тело по схемам запросов и ``Idempotency-Key`` у каждой мутации.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from admin_service.audit.models import AuditEntry

pytestmark = [pytest.mark.component, pytest.mark.django_db]

CASE = uuid.uuid4()
PLAYER = uuid.uuid4()
REPORTER = uuid.uuid4()
NOW = "2026-10-24T10:00:00Z"
SHA = "b" * 64
CASES = "/admin/v1/cases"
MODERATOR = ("admin.access", "social.moderate")
SENIOR = (*MODERATOR, "social.sanctions.manage", "social.sanctions.account")
XSS = "<script>alert(1)</script>"


def _key() -> str:
    return str(uuid.uuid4())


def _summary(**patch: Any) -> dict[str, Any]:
    return {
        "id": str(CASE),
        "target_kind": "comment",
        "target_id": str(uuid.uuid4()),
        "target_user_id": str(PLAYER),
        "status": "open",
        "reports_count": 3,
        "assignee_id": None,
        "opened_at": NOW,
        "version": 1,
    } | patch


def _detail(**patch: Any) -> dict[str, Any]:
    return (
        _summary()
        | {
            "reports": [
                {
                    "id": str(uuid.uuid4()),
                    "author_id": str(REPORTER),
                    "reason_code": "spam",
                    "text": XSS,
                    "context_match_id": None,
                    "created_at": NOW,
                }
            ],
            "target_preview": f"грубо {XSS}",
            "decision": None,
            "resolved_by": None,
            "resolved_at": None,
        }
        | patch
    )


def _sanction(**patch: Any) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "user_id": str(PLAYER),
        "kind": "chat",
        "started_at": NOW,
        "expires_at": None,
        "reason": "оскорбления",
        "actor_id": str(uuid.uuid4()),
        "revoked_at": None,
        "revoked_by": None,
        "revoke_reason": None,
        "active": True,
    } | patch


def _form(**patch: Any) -> dict[str, Any]:
    return {"reason": "решение по правилам", "idempotency_key": _key(), "version": 1} | patch


# ---------------------------------------------------------------------------- доступ


def test_hidden_and_forbidden_without_perm(client: Any, login: Any) -> None:
    login(perms=("admin.access", "media.ugc.moderate"), roles=("moderator",))
    page = client.get("/").content.decode()
    assert 'href="/social/"' not in page and 'href="/social/config/"' not in page
    for path in ("", f"cases/{CASE}/", "players/", f"players/{PLAYER}/", "config/", "config/edit/"):
        assert client.get(f"/social/{path}").status_code == 403
    assert client.post(f"/social/cases/{CASE}/claim/", _form()).status_code == 403
    assert client.post("/social/config/publish/", _form()).status_code == 403


def test_moderator_sees_queue_but_not_config(client: Any, login: Any, social: Any) -> None:
    login(perms=MODERATOR, roles=("moderator",))
    social.route("GET", CASES).respond(200, json={"items": [], "next_cursor": None})
    assert client.get("/social/").status_code == 200
    assert client.get("/social/config/").status_code == 403


# ---------------------------------------------------------------------------- очередь


def test_queue_lists_cases_with_filters_and_pagination(
    client: Any, login: Any, social: Any
) -> None:
    login()
    route = social.route("GET", CASES).respond_off_contract(
        200, json={"items": [_summary(status="in_work")], "next_cursor": "c&x=1"}
    )
    page = client.get("/social/?status=in_work&target_kind=comment&mine=on").content.decode()
    params = route.calls.last.request.url.params
    assert (params["status"], params["target_kind"], params["mine"]) == (
        "in_work",
        "comment",
        "true",
    )
    assert f"/social/cases/{CASE}/" in page and "cursor=c%26x%3D1" in page
    assert "status=in_work" in page  # фильтры сохраняются в ссылке «Дальше»


def test_queue_survives_service_error(client: Any, login: Any, social: Any, problem: Any) -> None:
    login()
    social.route("GET", CASES).respond(403, json=problem(403, "auth.forbidden"))
    response = client.get("/social/")
    assert response.status_code == 200 and "Недостаточно прав" in response.content.decode()


def test_case_card_escapes_user_texts_and_audits_view(client: Any, login: Any, social: Any) -> None:
    login()
    card = _detail()
    card["reports"][0]["reason_code"] = f"spam{XSS}"  # код вне контракта: всё равно экранируется
    social.route("GET", f"{CASES}/{CASE}").respond_off_contract(200, json=card)
    response = client.get(f"/social/cases/{CASE}/")
    page = response.content.decode()
    assert XSS not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "spam&lt;script&gt;" in page
    assert response["Cache-Control"] == "no-store"
    entry = AuditEntry.objects.get(action="social.view_case")
    assert entry.target_id == str(CASE) and XSS not in json.dumps(entry.after or {})


def test_case_card_404_is_error_page(client: Any, login: Any, social: Any, problem: Any) -> None:
    login()
    social.route("GET", f"{CASES}/{CASE}").respond(404, json=problem(404, "social.case_not_found"))
    assert client.get(f"/social/cases/{CASE}/").status_code == 404


def test_claim_sends_version_and_audits(client: Any, login: Any, social: Any) -> None:
    login()
    route = social.route("POST", f"{CASES}/{CASE}/claim").respond(
        200, json=_summary(status="in_work", version=2)
    )
    key = _key()
    response = client.post(f"/social/cases/{CASE}/claim/", _form(idempotency_key=key))
    assert response["Location"] == f"/social/cases/{CASE}/"
    assert json.loads(route.calls.last.request.content) == {"version": 1}
    assert route.calls.last.request.headers["Idempotency-Key"] == key
    entry = AuditEntry.objects.get(action="social.case_claim")
    assert (entry.service, entry.after) == (
        "social",
        {"status": "in_work", "version": 2, "decision": None},
    )


def test_claim_conflict_is_message_and_audited(
    client: Any, login: Any, social: Any, problem: Any
) -> None:
    login()
    social.route("POST", f"{CASES}/{CASE}/claim").respond(
        409, json=problem(409, "social.case_already_claimed")
    )
    assert client.post(f"/social/cases/{CASE}/claim/", _form()).status_code == 302
    entry = AuditEntry.objects.get(action="social.case_claim")
    assert (entry.outcome, entry.error_code) == (
        AuditEntry.Outcome.FAILED,
        "social.case_already_claimed",
    )


def test_escalate_requires_reason(client: Any, login: Any, social: Any) -> None:
    login()
    route = social.route("POST", f"{CASES}/{CASE}/escalate").respond(
        200, json=_summary(status="escalated", version=3)
    )
    social.route("GET", f"{CASES}/{CASE}").respond(200, json=_detail(status="in_work", version=2))
    assert client.post(f"/social/cases/{CASE}/escalate/", _form(reason="")).status_code == 400
    assert route.call_count == 0
    client.post(f"/social/cases/{CASE}/escalate/", _form(version=2, reason="не уверен"))
    assert json.loads(route.calls.last.request.content) == {"version": 2, "reason": "не уверен"}


def test_resolve_dismissed_and_content_removed(client: Any, login: Any, social: Any) -> None:
    login()
    route = social.route("POST", f"{CASES}/{CASE}/resolve").respond(
        200, json=_detail(status="resolved", decision="dismissed", version=3)
    )
    client.post(f"/social/cases/{CASE}/resolve/", _form(version=2, decision="dismissed"))
    assert json.loads(route.calls.last.request.content) == {
        "version": 2,
        "decision": "dismissed",
        "reason": "решение по правилам",
    }
    client.post(f"/social/cases/{CASE}/resolve/", _form(version=2, decision="content_removed"))
    assert json.loads(route.calls.last.request.content)["decision"] == "content_removed"
    assert AuditEntry.objects.filter(action="social.case_resolve").count() == 2


def test_resolve_sanctioned_sends_sanction_and_validates(
    client: Any, login: Any, social: Any
) -> None:
    login()
    route = social.route("POST", f"{CASES}/{CASE}/resolve").respond(
        200, json=_detail(status="resolved", decision="sanctioned", version=3)
    )
    url = f"/social/cases/{CASE}/resolve/"
    social.route("GET", f"{CASES}/{CASE}").respond(200, json=_detail(status="in_work", version=2))
    # Без вида и причины санкции форма отклоняется до обращения к сервису.
    assert client.post(url, _form(version=2, decision="sanctioned")).status_code == 400
    # Санкция при другом решении - тоже.
    bad = _form(version=2, decision="dismissed", sanction_kind="chat")
    assert client.post(url, bad).status_code == 400
    assert route.call_count == 0
    good = _form(
        version=2,
        decision="sanctioned",
        sanction_kind="chat",
        sanction_duration_s=3600,
        sanction_reason="оскорбления",
    )
    client.post(url, good)
    assert json.loads(route.calls.last.request.content)["sanction"] == {
        "kind": "chat",
        "duration_s": 3600,
        "reason": "оскорбления",
    }


def test_account_sanction_in_resolve_needs_senior(client: Any, login: Any, social: Any) -> None:
    login(perms=(*MODERATOR, "social.sanctions.manage"), roles=("moderator",))
    route = social.route("POST", f"{CASES}/{CASE}/resolve").respond(200, json=_detail())
    social.route("GET", f"{CASES}/{CASE}").respond(200, json=_detail(status="in_work", version=2))
    data = _form(
        version=2, decision="sanctioned", sanction_kind="account", sanction_reason="читерство"
    )
    assert client.post(f"/social/cases/{CASE}/resolve/", data).status_code == 400
    assert route.call_count == 0


def test_sanctioned_resolve_needs_sanction_permission(client: Any, login: Any, social: Any) -> None:
    login(perms=MODERATOR, roles=("moderator",))
    route = social.route("POST", f"{CASES}/{CASE}/resolve").respond(200, json=_detail())
    social.route("GET", f"{CASES}/{CASE}").respond(200, json=_detail(status="in_work", version=2))
    data = _form(
        version=2, decision="sanctioned", sanction_kind="chat", sanction_reason="оскорбления"
    )
    assert client.post(f"/social/cases/{CASE}/resolve/", data).status_code == 400
    assert route.call_count == 0


def test_escalated_case_hides_resolve_from_regular_moderator(
    client: Any, login: Any, social: Any
) -> None:
    social.route("GET", f"{CASES}/{CASE}").respond(200, json=_detail(status="escalated", version=3))
    login(perms=MODERATOR, roles=("moderator",))
    page = client.get(f"/social/cases/{CASE}/").content.decode()
    assert "решает только старший" in page and 'value="Решить"' not in page


def test_escalated_case_shows_resolve_to_senior(client: Any, login: Any, social: Any) -> None:
    social.route("GET", f"{CASES}/{CASE}").respond(200, json=_detail(status="escalated", version=3))
    login(perms=SENIOR, roles=("admin",))
    assert 'value="Решить"' in client.get(f"/social/cases/{CASE}/").content.decode()


# ---------------------------------------------------------------------------- игрок


def _moderation(**patch: Any) -> dict[str, Any]:
    return {
        "user_id": str(PLAYER),
        "nickname": "Bob",
        "counters": {
            "forfeits": 1,
            "leaves": 2,
            "tech_losses": 0,
            "reports_received": 5,
            "praises": 7,
            "matches_total": 40,
        },
        "sanctions": [_sanction()],
        "cases": [_summary()],
    } | patch


def test_player_card_shows_counters_sanctions_and_cases(
    client: Any, login: Any, social: Any
) -> None:
    login()
    social.route("GET", f"/admin/v1/users/{PLAYER}/moderation").respond(200, json=_moderation())
    page = client.get(f"/social/players/{PLAYER}/").content.decode()
    assert "Bob" in page and "40" in page and "оскорбления" in page
    assert f"/social/cases/{CASE}/" in page and "автоматических санкций нет" in page
    assert AuditEntry.objects.filter(action="social.view_player").exists()


def test_player_search_redirects_to_card(client: Any, login: Any) -> None:
    login()
    response = client.get(f"/social/players/?user_id={PLAYER}")
    assert response["Location"] == f"/social/players/{PLAYER}/"
    assert client.get("/social/players/").status_code == 200


def test_grant_sanction_posts_to_social_and_audits(client: Any, login: Any, social: Any) -> None:
    login()
    route = social.route("POST", f"/admin/v1/users/{PLAYER}/sanctions").respond(
        201, json=_sanction(kind="online")
    )
    key = _key()
    response = client.post(
        f"/social/players/{PLAYER}/sanctions/",
        {"kind": "online", "duration_s": 3600, "reason": "срыв матчей", "idempotency_key": key},
    )
    assert response["Location"] == f"/social/players/{PLAYER}/"
    assert json.loads(route.calls.last.request.content) == {
        "kind": "online",
        "duration_s": 3600,
        "reason": "срыв матчей",
    }
    assert route.calls.last.request.headers["Idempotency-Key"] == key
    entry = AuditEntry.objects.get(action="social.sanction_grant")
    assert entry.reason == "срыв матчей" and entry.target_id == str(PLAYER)


def test_grant_permanent_sends_null_duration(client: Any, login: Any, social: Any) -> None:
    login()
    route = social.route("POST", f"/admin/v1/users/{PLAYER}/sanctions").respond(
        201, json=_sanction()
    )
    client.post(
        f"/social/players/{PLAYER}/sanctions/",
        {"kind": "chat", "duration_s": "", "reason": "спам", "idempotency_key": _key()},
    )
    assert json.loads(route.calls.last.request.content)["duration_s"] is None


def test_grant_requires_permission_reason_and_senior_for_account(
    client: Any, login: Any, social: Any
) -> None:
    login(perms=(*MODERATOR, "social.sanctions.manage"), roles=("moderator",))
    url = f"/social/players/{PLAYER}/sanctions/"
    data = {"kind": "chat", "duration_s": 3600, "reason": "спам", "idempotency_key": _key()}
    route = social.route("POST", f"/admin/v1/users/{PLAYER}/sanctions").respond(
        201, json=_sanction()
    )
    social.route("GET", f"/admin/v1/users/{PLAYER}/moderation").respond(200, json=_moderation())
    assert client.post(url, data | {"reason": ""}).status_code == 400
    assert client.post(url, data | {"kind": "account"}).status_code == 400
    assert route.call_count == 0


def test_grant_without_sanction_permission_is_forbidden(client: Any, login: Any) -> None:
    login(perms=MODERATOR, roles=("moderator",))
    data = {"kind": "chat", "duration_s": 3600, "reason": "спам", "idempotency_key": _key()}
    assert client.post(f"/social/players/{PLAYER}/sanctions/", data).status_code == 403


def test_grant_self_sanction_refusal_is_audited(
    client: Any, login: Any, social: Any, problem: Any
) -> None:
    login()
    social.route("POST", f"/admin/v1/users/{PLAYER}/sanctions").respond(
        403, json=problem(403, "social.self_sanction_forbidden")
    )
    client.post(
        f"/social/players/{PLAYER}/sanctions/",
        {"kind": "chat", "duration_s": 3600, "reason": "тест", "idempotency_key": _key()},
    )
    entry = AuditEntry.objects.get(action="social.sanction_grant")
    assert entry.error_code == "social.self_sanction_forbidden"


def test_revoke_sanction(client: Any, login: Any, social: Any) -> None:
    login()
    sanction = uuid.uuid4()
    route = social.route("POST", f"/admin/v1/sanctions/{sanction}/revoke").respond(
        200, json=_sanction(id=str(sanction), active=False)
    )
    client.post(
        f"/social/players/{PLAYER}/sanctions/revoke/",
        {"sanction_id": str(sanction), "reason": "ошибочно", "idempotency_key": _key()},
    )
    assert json.loads(route.calls.last.request.content) == {"reason": "ошибочно"}
    entry = AuditEntry.objects.get(action="social.sanction_revoke")
    assert entry.target_id == str(sanction) and entry.before == {"user_id": str(PLAYER)}


# ---------------------------------------------------------------------------- конфиг

CONFIG = {"friends": {"max_friends": 200}, "comments": {"max_length": 500}}


def _card(version: int, status: str) -> dict[str, Any]:
    return {
        "version": version,
        "status": status,
        "created_at": NOW,
        "created_by": str(uuid.uuid4()),
        "body_sha256": SHA,
        "published_at": NOW if status != "draft" else None,
    }


def _versions(social: Any) -> None:
    social.route("GET", "/admin/v1/config/versions").respond(
        200, json={"items": [_card(2, "draft"), _card(1, "published")]}
    )
    for version, status in ((1, "published"), (2, "draft")):
        social.route("GET", f"/admin/v1/config/versions/{version}").respond(
            200, json={"card": _card(version, status), "config": CONFIG}
        )


def test_config_page_shows_versions_and_publish_form_with_hash(
    client: Any, login: Any, social: Any
) -> None:
    login()
    _versions(social)
    page = client.get("/social/config/").content.decode()
    assert "v2" in page and "v1" in page and SHA in page
    assert 'name="body_sha256"' in page and 'value="2"' in page


def test_publish_sends_version_and_hash(client: Any, login: Any, social: Any) -> None:
    login()
    route = social.route("POST", "/admin/v1/config/draft/publish").respond(
        200, json={"card": _card(2, "published"), "config": CONFIG}
    )
    key = _key()
    response = client.post(
        "/social/config/publish/",
        {"version": 2, "body_sha256": SHA, "reason": "новый лимит", "idempotency_key": key},
    )
    assert response["Location"] == "/social/config/"
    assert json.loads(route.calls.last.request.content) == {
        "version": 2,
        "body_sha256": SHA,
        "reason": "новый лимит",
    }
    entry = AuditEntry.objects.get(action="social.publish_config")
    assert entry.before == {"draft_version": 2, "body_sha256": SHA}


def test_config_page_without_publish_perm_has_no_publish_or_rollback(
    client: Any, login: Any, social: Any
) -> None:
    login(perms=(*MODERATOR, "social.config.manage"), roles=("moderator",))
    _versions(social)
    page = client.get("/social/config/").content.decode()
    assert "v2" in page and SHA in page  # версии читать можно
    assert 'name="body_sha256"' not in page
    assert "/social/config/publish/" not in page and "/social/config/rollback/" not in page
    assert "/social/config/edit/" in page  # черновик править можно


def test_publish_without_publish_permission_is_forbidden(client: Any, login: Any) -> None:
    login(perms=("admin.access", "social.config.manage"), roles=("moderator",))
    data = {"version": 2, "body_sha256": SHA, "reason": "новый лимит", "idempotency_key": _key()}
    assert client.post("/social/config/publish/", data).status_code == 403


def test_draft_hash_conflict_is_audited(client: Any, login: Any, social: Any, problem: Any) -> None:
    login()
    social.route("POST", "/admin/v1/config/draft/publish").respond(
        409, json=problem(409, "social.config_draft_changed")
    )
    client.post(
        "/social/config/publish/",
        {"version": 2, "body_sha256": SHA, "reason": "новый лимит", "idempotency_key": _key()},
    )
    assert AuditEntry.objects.get(action="social.publish_config").error_code == (
        "social.config_draft_changed"
    )


def test_rollback_posts_reason(client: Any, login: Any, social: Any) -> None:
    login()
    route = social.route("POST", "/admin/v1/config/versions/1/rollback").respond(
        201, json={"card": _card(3, "draft"), "config": CONFIG}
    )
    client.post(
        "/social/config/rollback/",
        {"to_version": 1, "reason": "откат лимита", "idempotency_key": _key()},
    )
    assert json.loads(route.calls.last.request.content) == {"reason": "откат лимита"}
    assert AuditEntry.objects.filter(action="social.rollback_config").exists()


def test_editor_saves_draft_and_shows_422_errors_with_lines(
    client: Any, login: Any, social: Any, problem: Any
) -> None:
    login()
    _versions(social)
    page = client.get("/social/config/edit/").content.decode()
    assert "max_friends" in page and "friends" in page  # текст черновика и разделы-подсказка
    route = social.route("PUT", "/admin/v1/config/draft").respond(
        200, json={"card": _card(2, "draft"), "config": CONFIG}
    )
    text = "friends:\n  max_friends: 200\ncomments:\n  max_length: 500\n"
    data = {"fmt": "yaml", "text": text, "reason": "правка", "idempotency_key": _key()}
    assert client.post("/social/config/edit/", data)["Location"] == "/social/config/"
    assert json.loads(route.calls.last.request.content) == {"config": CONFIG, "reason": "правка"}

    invalid = problem(422, "social.config_invalid") | {
        "details": {"errors": [{"path": "friends/max_friends", "reason": "должно быть >= 1"}]}
    }
    social.route("PUT", "/admin/v1/config/draft").respond_off_contract(422, json=invalid)
    bad = text.replace("200", "0")
    response = client.post("/social/config/edit/", data | {"text": bad, "idempotency_key": _key()})
    page = response.content.decode()
    assert response.status_code == 200
    assert "должно быть &gt;= 1" in page and "max_friends: 0" in page


def test_editor_rejects_broken_text_without_service_call(
    client: Any, login: Any, social: Any
) -> None:
    login()
    route = social.route("PUT", "/admin/v1/config/draft").respond(
        200, json={"card": _card(2, "draft"), "config": CONFIG}
    )
    data = {"fmt": "yaml", "text": "a: [", "reason": "правка", "idempotency_key": _key()}
    assert client.post("/social/config/edit/", data).status_code == 400
    assert route.call_count == 0
