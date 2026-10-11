"""Представления раздела «Наборы дня»: календарь, замена единицы, перегенерация (D6).

Наборы создаёт game (периодическая задача на ``schedule_ahead_days`` вперёд); админ видит
будущие наборы и до начала дня заменяет единицы. ``locked`` (день начался) — только чтение:
кнопки скрыты, а если форма всё же отправлена (вкладка открыта до полуночи), game ответит
409 ``game.daily_locked`` — сообщение и аудит отказа.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from admin_service.apps.game_daily.forms import (
    CANDIDATES_LIMIT,
    CalendarForm,
    CandidateSearchForm,
    RegenerateForm,
    ReplaceItemForm,
)
from admin_service.core.game_client import GAME, GameAdminClient
from admin_service.core.permissions import has_perm, require_perm
from admin_service.core.views import mutate, read

VIEW, CURATE = "game.daily.view", "game.daily.curate"
MODES_VIEW = "config.modes.view"
_EDITABLE = frozenset({"scheduled"})
_REGENERABLE = frozenset({"scheduled", "failed"})
_NOT_FOUND = 404


def today() -> date:
    """«Сегодня» в часовом поясе смены дня (``GV_ADMIN__DAILY_TIMEZONE``)."""
    return datetime.now(ZoneInfo(settings.GV_DAILY_TIMEZONE)).date()


def _list(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _calendar_url(key: str, start: date) -> str:
    return f"{reverse('game_daily:calendar')}?{urlencode({'mode_key': key, 'start': start})}"


def _mode_keys(request: HttpRequest) -> list[str]:
    """Ключи режимов для подсказки в поле (без права на конфиги — пусто, поле вводится руками)."""
    if not has_perm(request, MODES_VIEW):
        return []
    rows, _ = read(request, GAME, lambda c: GameAdminClient(c).mode_configs())
    return sorted(str(r.get("key")) for r in _list(rows) if r.get("key"))


def _sets_view(key: str, rows: Any, *, curate: bool) -> list[dict[str, Any]]:
    """Наборы для шаблона: ссылки на замену и перегенерацию — только где это разрешено."""
    result = []
    for row in _list(rows):
        day, status = _day(row.get("day")), str(row.get("status") or "")
        editable = curate and day is not None and status in _EDITABLE
        items = []
        for item in _list(row.get("items")):
            index = item.get("index")
            url = (
                reverse("game_daily:item", kwargs={"key": key, "day": day, "index": index})
                if editable and isinstance(index, int) and index >= 0
                else None
            )
            items.append({**item, "replace_url": url})
        result.append(
            {
                **row,
                "items": items,
                "locked": status not in _EDITABLE,
                "regenerate_url": reverse("game_daily:regenerate", kwargs={"key": key, "day": day})
                if curate and day is not None and status in _REGENERABLE
                else None,
            }
        )
    return result


@require_GET
@require_perm(VIEW)
def calendar(request: HttpRequest) -> HttpResponse:
    """Календарь наборов режима за диапазон дней: статус, версия режима, состав."""
    form = CalendarForm(request.GET or None)
    sets: list[dict[str, Any]] = []
    error: str | None = None
    if form.is_bound and form.is_valid():
        key = form.cleaned_data["mode_key"]
        start, end = form.resolve(today())
        rows, exc = read(request, GAME, lambda c: GameAdminClient(c).daily_sets(key, start, end))
        error = exc.message if exc else None
        sets = _sets_view(key, rows, curate=has_perm(request, CURATE))
    return render(
        request,
        "admin_game_daily/calendar.html",
        {
            "title": "Наборы дня",
            "form": form,
            "mode_keys": _mode_keys(request),
            "sets": sets,
            "error": error,
            "regenerate_form": RegenerateForm(),
            "today": today(),
        },
        status=400 if form.is_bound and form.errors else 200,
    )


def _find(rows: Any, day: date, index: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for row in _list(rows):
        if _day(row.get("day")) == day:
            item = next((i for i in _list(row.get("items")) if i.get("index") == index), None)
            return row, item
    return None, None


@require_http_methods(["GET", "POST"])
@require_perm(CURATE)
def replace_item(request: HttpRequest, key: str, day: date, index: int) -> HttpResponse:
    """Замена единицы набора: поиск кандидатов в проекции game и отправка с причиной."""
    rows, exc = read(request, GAME, lambda c: GameAdminClient(c).daily_sets(key, day, day))
    daily_set, item = _find(rows, day, index)

    if request.method == "POST":
        form = ReplaceItemForm(request.POST)
        if form.is_valid():
            reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
            new_id: UUID = form.cleaned_data["media_item_id"]
            ok, _ = mutate(
                request,
                service=GAME,
                action="game.replace_daily_item",
                target_type="daily_set",
                target_id=f"{key}/{day.isoformat()}",
                reason=reason,
                idempotency_key=idem,
                before={"index": index, "media_item_id": (item or {}).get("media_item_id")},
                audit_after={"index": index, "media_item_id": str(new_id)},
                call=lambda c: GameAdminClient(c).replace_item(
                    key, day, index, media_item_id=new_id, reason=reason, idempotency_key=idem
                ),
                success="Единица заменена",
            )
            if ok:
                return redirect(_calendar_url(key, day))
            return redirect(request.get_full_path())
        status = 400
    else:
        form = ReplaceItemForm()
        status = 200

    search = CandidateSearchForm(request.GET or None)
    candidates: list[dict[str, Any]] = []
    search_error: str | None = None
    if search.is_bound and search.is_valid() and search.cleaned_data["q"]:
        found, search_exc = read(
            request,
            GAME,
            lambda c: GameAdminClient(c).projection_items(
                q=search.cleaned_data["q"],
                domain=key.split(".", 1)[0],
                playable_for_mode=key,
                limit=CANDIDATES_LIMIT,
            ),
        )
        search_error = search_exc.message if search_exc else None
        for row in _list(found):
            if _uuid(row.get("media_item_id")) is None:
                continue
            row["selectable"] = row.get("status") == "active" and row.get("playable") is not False
            candidates.append(row)

    if exc is None and daily_set is None:
        status = _NOT_FOUND
    return render(
        request,
        "admin_game_daily/replace_item.html",
        {
            "title": f"Замена: {key}, {day.isoformat()}, раунд {index + 1}",
            "key": key,
            "day": day,
            "index": index,
            "daily_set": daily_set,
            "item": item,
            "locked": daily_set is not None and daily_set.get("status") not in _EDITABLE,
            "error": exc.message if exc else None,
            "search": search,
            "candidates": candidates,
            "search_error": search_error,
            "form": form,
            "back_url": _calendar_url(key, day),
        },
        status=status,
    )


@require_POST
@require_perm(CURATE)
def regenerate(request: HttpRequest, key: str, day: date) -> HttpResponse:
    """Перегенерировать набор (``scheduled`` или ``failed``) по правилам пула режима."""
    form = RegenerateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Укажите причину перегенерации (от 3 символов)")
        return redirect(_calendar_url(key, day))
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    mutate(
        request,
        service=GAME,
        action="game.regenerate_daily_set",
        target_type="daily_set",
        target_id=f"{key}/{day.isoformat()}",
        reason=reason,
        idempotency_key=idem,
        audit_fields=("status", "mode_version"),
        call=lambda c: GameAdminClient(c).regenerate(key, day, reason=reason, idempotency_key=idem),
        success="Набор перегенерирован",
    )
    return redirect(_calendar_url(key, day))
