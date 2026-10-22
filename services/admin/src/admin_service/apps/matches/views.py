"""Представления раздела «Матчи»: активные комнаты, принудительное завершение, итог матча.

Комнаты и матчи хранит match: чтение и команды — только через его admin-API
(``core.match_client``, план этапа 4 §2.5). Право — ``match.manage`` (identity ``0008``):
проверяется здесь, чтобы не показывать формы зря, и ещё раз в match. Каждое действие
пишет ``AuditEntry``: завершение — и при успехе, и при отказе (``core.views.mutate``),
просмотр списка и итога (никнеймы и id игроков) — ``audit_read``.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from admin_service.apps.matches.forms import AbortForm, MatchLookupForm, RoomFilterForm
from admin_service.core.converters import RoomCodeConverter
from admin_service.core.match_client import MATCH, MatchAdminClient
from admin_service.core.permissions import require_perm
from admin_service.core.views import audit_read, mutate, read

MANAGE = "match.manage"
_NOT_FOUND = 404
_ROOM_CODE = re.compile(RoomCodeConverter.regex)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None


def _room_row(row: dict[str, Any]) -> dict[str, Any]:
    """Строка комнаты и ссылки — только для корректных кода и id (ответ не роняет страницу)."""
    code = str(row.get("code") or "")
    match_id = _uuid(row.get("match_id"))
    return {
        **row,
        "mode": _dict(row.get("mode")),
        "abort_url": reverse("matches:abort", kwargs={"code": code})
        if _ROOM_CODE.fullmatch(code)
        else None,
        "match_url": reverse("matches:match", kwargs={"match_id": match_id}) if match_id else None,
    }


# ---------------------------------------------------------------------------- комнаты


@require_GET
@require_perm(MANAGE)
def room_list(request: HttpRequest) -> HttpResponse:
    """Активные комнаты (все фазы, кроме ``closed``) с фильтром по фазе и курсором."""
    form = RoomFilterForm(request.GET or None)
    filters: dict[str, Any] = {}  # Any: значения формы (строки), ключи — параметры клиента
    if form.is_bound and form.is_valid():
        filters = {k: v for k, v in form.cleaned_data.items() if v}
    page, error = read(request, MATCH, lambda c: MatchAdminClient(c).rooms(**filters))
    page = _dict(page)
    rooms = [_room_row(r) for r in _list(page.get("items"))]
    if error is None:
        audit_read(
            request,
            action="match.view_rooms",
            service=MATCH,
            target_type="room_list",
            target_id="",
            phase=filters.get("phase"),
            rooms=[str(r.get("code")) for r in rooms],
        )
    next_cursor = page.get("next_cursor")
    params = {k: v for k, v in filters.items() if k != "cursor"}
    return render(
        request,
        "admin_matches/room_list.html",
        {
            "title": "Матчи: активные комнаты",
            "form": form,
            "rooms": rooms,
            "next_url": f"?{urlencode({**params, 'cursor': next_cursor})}"
            if isinstance(next_cursor, str) and next_cursor
            else None,
            "error": error.message if error else None,
            "lookup_form": MatchLookupForm(),
        },
        status=400 if form.is_bound and form.errors else 200,
    )


@require_http_methods(["GET", "POST"])
@require_perm(MANAGE)
def room_abort(request: HttpRequest, code: str) -> HttpResponse:
    """Принудительное завершение комнаты: подтверждение с обязательной причиной.

    match отвечает 202 — команда принята владельцем комнаты (ADR 0034), матч завершится
    с ``finish = aborted``. 404 ``match.room_not_found`` / 409 ``match.room_closed`` —
    сообщение и аудит отказа.
    """
    form = AbortForm(request.POST if request.method == "POST" else None)
    if request.method == "GET" or not form.is_valid():
        return render(
            request,
            "admin_matches/room_abort.html",
            {"title": f"Завершить комнату {code}", "code": code, "form": form},
            status=400 if form.is_bound else 200,
        )
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    mutate(
        request,
        service=MATCH,
        action="match.abort_room",
        target_type="room",
        target_id=code,
        reason=reason,
        idempotency_key=idem,
        audit_fields=("accepted", "version"),
        call=lambda c: MatchAdminClient(c).abort(code, reason=reason, idempotency_key=idem),
        success=f"Комната {code}: команда завершения принята, матч завершится как aborted",
    )
    return redirect("matches:rooms")


# ---------------------------------------------------------------------------- итог матча


@require_GET
@require_perm(MANAGE)
def match_lookup(request: HttpRequest) -> HttpResponse:
    """Переход к итогу по id из формы на странице комнат (некорректный id — назад к списку)."""
    form = MatchLookupForm(request.GET)
    if not form.is_valid():
        return redirect("matches:rooms")
    return redirect("matches:match", match_id=form.cleaned_data["match_id"])


@require_GET
@require_perm(MANAGE)
def match_detail(request: HttpRequest, match_id: UUID) -> HttpResponse:
    """Итог матча: режим, способ завершения, места игроков и раунды."""
    result, error = read(request, MATCH, lambda c: MatchAdminClient(c).match(match_id))
    not_found = error is not None and error.status == _NOT_FOUND
    result = _dict(result) if error is None else {}
    if error is None:
        audit_read(
            request,
            action="match.view_result",
            service=MATCH,
            target_type="match",
            target_id=str(match_id),
        )
    players = sorted(_list(result.get("players")), key=lambda p: _sort_int(p.get("place")))
    nicknames = {str(p.get("user_id")): p.get("nickname") for p in players}
    rounds = [
        {
            **r,
            "catalog_url": _catalog_url(r.get("media_item_id")),
            "results": [
                {**x, "nickname": nicknames.get(str(x.get("user_id"))) or x.get("user_id")}
                for x in _list(r.get("results"))
            ],
        }
        for r in _list(result.get("rounds"))
    ]
    return render(
        request,
        "admin_matches/match_detail.html",
        {
            "title": f"Матч {match_id}",
            "match_id": match_id,
            "result": result or None,
            "mode": _dict(result.get("mode")),
            "players": players,
            "rounds": rounds,
            "not_found": not_found,
            "error": None if error is None or not_found else error.message,
        },
        status=_NOT_FOUND if not_found else 200,
    )


def _catalog_url(value: Any) -> str | None:
    item_id = _uuid(value)
    return reverse("catalog:item", kwargs={"item_id": item_id}) if item_id else None


def _sort_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 99
