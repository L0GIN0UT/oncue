"""«Прогрессия»: привязка публикации к хешу, владелец проводки из ответа, id форм, «нет данных»."""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from admin_service.audit.models import AuditEntry

pytestmark = [pytest.mark.component, pytest.mark.django_db]

PLAYER = uuid.uuid4()
USER = uuid.uuid4()
NOW = "2026-10-23T10:00:00Z"
SHA = "b" * 64
LEDGER = f"/admin/v1/users/{PLAYER}/ledger"
CONFIG = {"timezone": "Europe/Moscow", "levels": {"base": 500, "step": 20, "max_level": 100}}


def _key() -> str:
    return str(uuid.uuid4())


def _card(version: int, status: str) -> dict[str, Any]:
    return {
        "version": version,
        "status": status,
        "created_at": NOW,
        "created_by": str(USER),
        "published_at": NOW if status != "draft" else None,
        "body_sha256": SHA,
    }


def _entry(op_id: str, **patch: Any) -> dict[str, Any]:
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


def test_reverse_audit_and_redirect_use_user_from_service_response(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    owner = uuid.uuid4()  # владелец проводки не совпадает с игроком из URL
    progression.route("POST", "/admin/v1/ledger/solo%3A1/reverse").respond(
        201,
        json={
            "user_id": str(owner),
            "entry": _entry("admin:r", direction="debit", reversal_of="solo:1"),
            "balance_after": 7,
        },
    )
    response = client.post(
        f"/progression/ledger/{PLAYER}/reverse/",
        {"op_id": "solo:1", "reason": "ошибочное начисление", "idempotency_key": _key()},
    )
    assert response["Location"] == f"/progression/ledger/{owner}/"
    entry = AuditEntry.objects.get(action="progression.reverse")
    assert entry.after == {"user_id": str(owner), "balance_after": 7}


def test_publish_form_carries_body_hash_shown_in_list(
    client: Any, login: Any, progression: Any
) -> None:
    login()
    progression.route("GET", "/admin/v1/configs").respond(
        200, json={"items": [_card(2, "draft"), _card(1, "published")]}
    )
    for number, status in ((1, "published"), (2, "draft")):
        progression.route("GET", f"/admin/v1/configs/{number}").respond(
            200, json={"card": _card(number, status), "config": CONFIG}
        )
    page = client.get("/progression/").content.decode()
    assert f'name="body_sha256" value="{SHA}"' in page


def test_ledger_forms_have_unique_html_ids(client: Any, login: Any, progression: Any) -> None:
    login()
    progression.route("GET", LEDGER).respond(
        200,
        json={
            "user_id": str(PLAYER),
            "earned": 6,
            "paid": 0,
            "items": [_entry("solo:1"), _entry("solo:2")],
            "next_cursor": None,
        },
    )
    page = client.get(f"/progression/ledger/{PLAYER}/").content.decode()
    ids = re.findall(r'\sid="([^"]+)"', page)
    assert len(ids) == len(set(ids))


def test_debit_entry_has_no_reverse_button(client: Any, login: Any, progression: Any) -> None:
    login()
    progression.route("GET", LEDGER).respond(
        200,
        json={
            "user_id": str(PLAYER),
            "earned": 0,
            "paid": 0,
            "items": [_entry("shop:1", direction="debit")],
            "next_cursor": None,
        },
    )
    page = client.get(f"/progression/ledger/{PLAYER}/").content.decode()
    assert 'value="Сторнировать"' not in page


def test_totals_without_data_are_marked(client: Any, login: Any, progression: Any) -> None:
    login()
    progression.route("GET", "/admin/v1/quests").respond(
        200,
        json={
            "config_version": 1,
            "items": [
                {
                    "key": "play_3_solo",
                    "period": "daily",
                    "title": "Три раунда",
                    "reward": {"xp": 1, "verses": 1},
                    "enabled": True,
                    "completed_total": None,
                }
            ],
        },
    )
    progression.route("GET", "/admin/v1/badges").respond(
        200,
        json={
            "config_version": 1,
            "items": [{"key": "b", "title": "Б", "enabled": True, "unlocked_total": None}],
        },
    )
    page = client.get("/progression/quests/").content.decode()
    assert page.count("нет данных") == 2
