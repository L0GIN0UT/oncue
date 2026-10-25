"""«Модерация»: сигналы win-trading в очереди и карточке игрока (ADR 0068). Только показ."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from admin_service.audit.models import AuditEntry

pytestmark = [pytest.mark.component, pytest.mark.django_db]

PLAYER = uuid.uuid4()
OTHER = uuid.uuid4()
NOW = "2026-10-24T10:00:00Z"
SIGNALS = "/admin/v1/win-trading/signals"


def _signal(**patch: Any) -> dict[str, Any]:
    return {
        "kind": "pair_repeat",
        "user_id": str(PLAYER),
        "other_user_id": str(OTHER),
        "count": 6,
        "streak": 4,
        "severity": "high",
        "window_s": 604800,
        "first_at": NOW,
        "last_at": NOW,
        "details": {"wins": {str(PLAYER): 5, str(OTHER): 1}},
    } | patch


def test_queue_shows_signals_with_filters_and_audit(client: Any, login: Any, social: Any) -> None:
    login()
    route = social.route("GET", SIGNALS).respond_off_contract(
        200, json={"items": [_signal()], "next_cursor": "c&x=1"}
    )
    page = client.get(f"/social/win-trading/?kind=pair_repeat&severity=high&user_id={PLAYER}")
    body = page.content.decode()
    params = route.calls.last.request.url.params
    assert (params["kind"], params["severity"], params["user_id"]) == (
        "pair_repeat",
        "high",
        str(PLAYER),
    )
    assert f"/social/players/{PLAYER}/" in body and "<td>168</td>" in body
    assert "cursor=c%26x%3D1" in body and "kind=pair_repeat" in body
    assert "автоматических санкций нет" in body
    assert page["Cache-Control"] == "no-store"
    assert AuditEntry.objects.filter(action="social.view_win_trading").exists()


def test_queue_requires_moderate_permission(client: Any, login: Any) -> None:
    login(perms=("admin.access", "media.ugc.moderate"), roles=("moderator",))
    assert client.get("/social/win-trading/").status_code == 403


def test_queue_survives_service_error(client: Any, login: Any, social: Any, problem: Any) -> None:
    login()
    social.route("GET", SIGNALS).respond(403, json=problem(403, "auth.forbidden"))
    response = client.get("/social/win-trading/")
    assert response.status_code == 200 and "Недостаточно прав" in response.content.decode()


def test_player_card_shows_win_trading_block(client: Any, login: Any, social: Any) -> None:
    login()
    social.route("GET", f"/admin/v1/users/{PLAYER}/moderation").respond(
        200,
        json={
            "user_id": str(PLAYER),
            "nickname": "Bob",
            "counters": {
                "forfeits": 0,
                "leaves": 0,
                "tech_losses": 0,
                "reports_received": 0,
                "praises": 0,
                "matches_total": 3,
            },
            "sanctions": [],
            "cases": [],
            "win_trading": [_signal()],
        },
    )
    page = client.get(f"/social/players/{PLAYER}/").content.decode()
    assert "Win-trading" in page and "pair_repeat" in page and str(OTHER) in page
