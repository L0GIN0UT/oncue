"""Представления раздела «События»: сезонные события, запуски, публикация, статистика.

Определения и запуски хранит progression (ADR 0054-0056); админка работает только через его
admin-API (``core.progression_client``). Права: ``progression.events.manage`` — раздел,
определение, карта, создание запуска (в том числе перезапуск); ``progression.events.publish`` —
публикация и остановка (кнопки без права скрыты, сервис проверяет всегда). Каждая мутация —
форма с обязательной причиной и ``Idempotency-Key`` + аудит (``core.views.mutate``). Тело
определения в аудит не пишется — только ключ, хеш и статусы.

Определение правится как YAML/JSON (как конфиг «Прогрессии»); находки на карте показываются
превью по ``x_pct``/``y_pct``. Публикация привязана к ``body_sha256``, который админ видел.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from admin_service.apps.events.forms import (
    EventKeyForm,
    MapForm,
    PublishRunForm,
    RunForm,
    StopRunForm,
    to_utc_iso,
)
from admin_service.apps.game_config import config_text
from admin_service.apps.game_config.forms import EditorForm
from admin_service.apps.progression.views import error_rows
from admin_service.core.payload import as_dict, as_list
from admin_service.core.permissions import has_perm, require_perm
from admin_service.core.progression_client import PROGRESSION, ProgressionAdminClient
from admin_service.core.service_client import ServiceClient, ServiceError
from admin_service.core.views import mutate, read, service_error_response

HTTP_NOT_FOUND = 404
MANAGE = "progression.events.manage"
PUBLISH = "progression.events.publish"
_INVALID_STATUSES = (400, 422)
_RUN_FIELDS = ("id", "seq", "status", "starts_at", "ends_at")
_DEFINITION_FIELDS = ("key", "body_sha256")

# Заготовка редактора нового события: поля и их порядок по event_definition.v1; значения —
# подсказка автору, проверку выполняет progression.
_NEW_EVENT: dict[str, Any] = {
    "title": "",
    "description": "",
    "theme_key": "",
    "finds": [
        {
            "key": "first_find",
            "number": 1,
            "title": "",
            "diary_before": "",
            "diary_after": "",
            "x_pct": 20,
            "y_pct": 40,
            "reward": {"xp": 0, "verses": 0},
            "final": False,
            "trigger": {"type": "ui_spot", "page_key": "home"},
        },
        {
            "key": "final_find",
            "number": 2,
            "title": "",
            "diary_before": "",
            "diary_after": "",
            "x_pct": 75,
            "y_pct": 55,
            "reward": {"xp": 0, "verses": 0},
            "final": True,
            "trigger": {"type": "ui_spot", "page_key": "home"},
        },
    ],
    "reward": {"item_key": ""},
}


def _client(call: Callable[[ProgressionAdminClient], Any]) -> Callable[[ServiceClient], Any]:
    return lambda c: call(ProgressionAdminClient(c))


def _capturing(
    call: Callable[[ProgressionAdminClient], Any], sink: list[ServiceError]
) -> Callable[[ServiceClient], Any]:
    def run(client: ServiceClient) -> Any:
        try:
            return call(ProgressionAdminClient(client))
        except ServiceError as exc:
            sink.append(exc)
            raise

    return run


def _event(request: HttpRequest, event_key: str) -> dict[str, Any]:
    """Событие из admin-API; отказ сервиса (в том числе 404) — ``ServiceError``."""
    data, error = read(request, PROGRESSION, _client(lambda c: c.event(event_key)))
    if error is not None:
        raise error
    return as_dict(data)


def _preview(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Точки для превью карты: только числа 0..100, остальное пропускается."""
    points: list[dict[str, Any]] = []
    for find in as_list(body.get("finds")):
        x, y = find.get("x_pct"), find.get("y_pct")
        if (
            isinstance(x, int | float)
            and isinstance(y, int | float)
            and not isinstance(x, bool)
            and not isinstance(y, bool)
        ):
            points.append(
                {
                    "number": find.get("number"),
                    "title": find.get("title"),
                    "final": bool(find.get("final")),
                    "x": min(max(float(x), 0.0), 100.0),
                    "y": min(max(float(y), 0.0), 100.0),
                }
            )
    return points


def _fail(request: HttpRequest, exc: ServiceError) -> HttpResponse:
    if exc.status == HTTP_NOT_FOUND:
        raise Http404(exc.message)
    return service_error_response(request, exc)


# ---------------------------------------------------------------------------- список


@require_GET
@require_perm(MANAGE)
def event_list(request: HttpRequest) -> HttpResponse:
    """Список событий со статусом последнего запуска."""
    data, error = read(request, PROGRESSION, _client(lambda c: c.events()))
    return render(
        request,
        "admin_events/list.html",
        {
            "title": "События",
            "error": error.message if error else None,
            "rows": as_list(as_dict(data).get("items")),
        },
    )


# ---------------------------------------------------------------------------- карточка


def _card_context(
    request: HttpRequest,
    event: dict[str, Any],
    *,
    run_form: RunForm | None = None,
    map_form: MapForm | None = None,
    publish_forms: dict[str, PublishRunForm] | None = None,
    stop_forms: dict[str, StopRunForm] | None = None,
) -> dict[str, Any]:
    body = as_dict(event.get("body"))
    runs = []
    for run in as_list(event.get("runs")):
        run_id = str(run.get("id"))
        status = run.get("status")
        runs.append(
            {
                **run,
                "publish_form": (publish_forms or {}).get(run_id)
                or (
                    PublishRunForm(
                        initial={"body_sha256": event.get("body_sha256")},
                        auto_id=f"id_pub_{run_id}_%s",
                    )
                    if status == "draft"
                    else None
                ),
                "stop_form": (stop_forms or {}).get(run_id)
                or (
                    StopRunForm(auto_id=f"id_stop_{run_id}_%s")
                    if status in ("published", "active")
                    else None
                ),
            }
        )
    return {
        "title": f"Событие {event.get('key')}",
        "event": event,
        "definition": body,
        "finds": as_list(body.get("finds")),
        "points": _preview(body),
        "runs": runs,
        "can_publish": has_perm(request, PUBLISH),
        "run_form": run_form or RunForm(auto_id="id_run_%s"),
        "map_form": map_form or _map_form(),
        "map_limit": int(settings.GV_SHOP_MEDIA_MAX_BYTES),
    }


def _map_form(*args: Any, **kwargs: Any) -> MapForm:
    return MapForm(
        *args,
        limit_bytes=int(settings.GV_SHOP_MEDIA_MAX_BYTES),
        auto_id="id_map_%s",
        **kwargs,
    )


def _card(request: HttpRequest, event_key: str, *, status: int = 200, **forms: Any) -> HttpResponse:
    try:
        event = _event(request, event_key)
    except ServiceError as exc:
        return _fail(request, exc)
    return render(
        request,
        "admin_events/card.html",
        _card_context(request, event, **forms),
        status=status,
    )


@require_GET
@require_perm(MANAGE)
def event_card(request: HttpRequest, event_key: str) -> HttpResponse:
    """Карточка: определение, превью точек на карте, запуски и действия над ними."""
    return _card(request, event_key)


# ---------------------------------------------------------------------------- редактор


def _render_editor(request: HttpRequest, *, status: int = 200, **context: Any) -> HttpResponse:
    return render(
        request,
        "admin_events/edit.html",
        {"title": "Событие: определение", **context},
        status=status,
    )


def _editor_response(
    request: HttpRequest,
    form: EditorForm,
    errors: list[ServiceError],
    *,
    key_form: EventKeyForm | None,
    event_key: str | None,
) -> HttpResponse:
    context: dict[str, Any] = {"form": form, "key_form": key_form, "event_key": event_key}
    text = str(form.data.get("text") or "")
    if errors and errors[0].status in _INVALID_STATUSES:
        rows = error_rows(text, details=errors[0].details, errors=errors[0].errors)
        marked = {r["line"] for r in rows if r["line"] is not None}
        context |= {"field_errors": rows, "lines": config_text.numbered(text, marked)}
    try:
        context["points"] = _preview(config_text.parse(text, form.cleaned_data.get("fmt", "yaml")))
    except (config_text.ConfigTextError, KeyError):
        context["points"] = []
    return _render_editor(request, status=400, **context)


@require_http_methods(["GET", "POST"])
@require_perm(MANAGE)
def event_new(request: HttpRequest) -> HttpResponse:
    """Создание события: ключ (неизменяем) и определение. Запусков нет до явного создания."""
    if request.method == "GET":
        form = EditorForm(initial={"fmt": "yaml", "text": config_text.dump(_NEW_EVENT, "yaml")})
        return _render_editor(
            request, form=form, key_form=EventKeyForm(), points=_preview(_NEW_EVENT)
        )
    key_form, form = EventKeyForm(request.POST), EditorForm(request.POST)
    if not (key_form.is_valid() and form.is_valid()) or form.body is None:
        return _render_editor(request, status=400, form=form, key_form=key_form, points=[])
    if not form.require_reason():
        return _render_editor(request, status=400, form=form, key_form=key_form, points=[])
    body, key = form.body, key_form.cleaned_data["key"]
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    errors: list[ServiceError] = []
    ok, _ = mutate(
        request,
        service=PROGRESSION,
        action="events.create",
        target_type="event",
        target_id=key,
        reason=reason,
        idempotency_key=idem,
        audit_fields=_DEFINITION_FIELDS,
        call=_capturing(
            lambda c: c.create_event(key=key, body=body, reason=reason, idempotency_key=idem),
            errors,
        ),
        success="Событие создано",
    )
    if ok:
        return redirect("events:card", event_key=key)
    return _editor_response(request, form, errors, key_form=key_form, event_key=None)


@require_http_methods(["GET", "POST"])
@require_perm(MANAGE)
def event_edit(request: HttpRequest, event_key: str) -> HttpResponse:
    """Правка определения; 409 ``event_definition_locked``, пока есть опубликованный запуск."""
    try:
        event = _event(request, event_key)
    except ServiceError as exc:
        return _fail(request, exc)
    if request.method == "GET":
        fmt: config_text.Format = "json" if request.GET.get("fmt") == "json" else "yaml"
        body = as_dict(event.get("body"))
        form = EditorForm(initial={"fmt": fmt, "text": config_text.dump(body, fmt)})
        return _render_editor(
            request, form=form, key_form=None, event_key=event_key, points=_preview(body)
        )
    form = EditorForm(request.POST)
    if not form.is_valid() or form.body is None or not form.require_reason():
        return _render_editor(
            request, status=400, form=form, key_form=None, event_key=event_key, points=[]
        )
    body = form.body
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    errors: list[ServiceError] = []
    ok, _ = mutate(
        request,
        service=PROGRESSION,
        action="events.update",
        target_type="event",
        target_id=event_key,
        reason=reason,
        idempotency_key=idem,
        before={"body_sha256": event.get("body_sha256")},
        audit_fields=_DEFINITION_FIELDS,
        call=_capturing(
            lambda c: c.update_event(event_key, body=body, reason=reason, idempotency_key=idem),
            errors,
        ),
        success="Определение сохранено",
    )
    if ok:
        return redirect("events:card", event_key=event_key)
    return _editor_response(request, form, errors, key_form=None, event_key=event_key)


# ---------------------------------------------------------------------------- карта


@require_POST
@require_perm(MANAGE)
def event_map(request: HttpRequest, event_key: str) -> HttpResponse:
    """Загрузка карты (статика PNG/WebP): размер проверяется до обращения к сервису."""
    form = _map_form(request.POST, request.FILES)
    if not form.is_valid():
        return _card(request, event_key, status=400, map_form=form)
    upload = form.cleaned_data["file"]
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    ok, _ = mutate(
        request,
        service=PROGRESSION,
        action="events.upload_map",
        target_type="event",
        target_id=event_key,
        reason=reason,
        idempotency_key=idem,
        audit_after={"bytes": upload.size, "content_type": upload.content_type},
        call=_client(
            lambda c: c.upload_event_map(
                event_key,
                reason=reason,
                file_name=upload.name or "map",
                file=upload.file,
                content_type=upload.content_type or "application/octet-stream",
                idempotency_key=idem,
            )
        ),
        success="Карта загружена",
    )
    if ok:
        return redirect("events:card", event_key=event_key)
    return _card(request, event_key, status=400, map_form=form)


# ---------------------------------------------------------------------------- запуски


@require_POST
@require_perm(MANAGE)
def run_new(request: HttpRequest, event_key: str) -> HttpResponse:
    """Новый draft-запуск с периодом; повторный запуск закончившегося события — это он же."""
    form = RunForm(request.POST, auto_id="id_run_%s")
    if not form.is_valid():
        return _card(request, event_key, status=400, run_form=form)
    starts, ends = (
        to_utc_iso(form.cleaned_data["starts_at"]),
        to_utc_iso(form.cleaned_data["ends_at"]),
    )
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    ok, _ = mutate(
        request,
        service=PROGRESSION,
        action="events.create_run",
        target_type="event",
        target_id=event_key,
        reason=reason,
        idempotency_key=idem,
        audit_fields=_RUN_FIELDS,
        call=_client(
            lambda c: c.create_run(
                event_key, starts_at=starts, ends_at=ends, reason=reason, idempotency_key=idem
            )
        ),
        success="Запуск создан (черновик)",
    )
    if ok:
        return redirect("events:card", event_key=event_key)
    return _card(request, event_key, status=400, run_form=form)


@require_POST
@require_perm(PUBLISH)
def run_publish(request: HttpRequest, event_key: str, run_id: UUID) -> HttpResponse:
    """Публикация запуска: подтверждение привязано к хешу определения с экрана."""
    form = PublishRunForm(request.POST, auto_id=f"id_pub_{run_id}_%s")
    if not form.is_valid():
        return _card(request, event_key, status=400, publish_forms={str(run_id): form})
    sha, reason = form.cleaned_data["body_sha256"], form.cleaned_data["reason"]
    idem = str(form.cleaned_data["idempotency_key"])
    ok, _ = mutate(
        request,
        service=PROGRESSION,
        action="events.publish_run",
        target_type="event_run",
        target_id=str(run_id),
        reason=reason,
        idempotency_key=idem,
        before={"event_key": event_key, "body_sha256": sha},
        audit_fields=_RUN_FIELDS,
        call=_client(
            lambda c: c.publish_run(
                event_key, run_id, body_sha256=sha, reason=reason, idempotency_key=idem
            )
        ),
        success="Запуск опубликован",
    )
    if ok:
        return redirect("events:card", event_key=event_key)
    return _card(request, event_key, status=400, publish_forms={str(run_id): form})


@require_POST
@require_perm(PUBLISH)
def run_stop(request: HttpRequest, event_key: str, run_id: UUID) -> HttpResponse:
    """Остановка активного или опубликованного запуска."""
    form = StopRunForm(request.POST, auto_id=f"id_stop_{run_id}_%s")
    if not form.is_valid():
        return _card(request, event_key, status=400, stop_forms={str(run_id): form})
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    ok, _ = mutate(
        request,
        service=PROGRESSION,
        action="events.stop_run",
        target_type="event_run",
        target_id=str(run_id),
        reason=reason,
        idempotency_key=idem,
        before={"event_key": event_key},
        audit_fields=_RUN_FIELDS,
        call=_client(lambda c: c.stop_run(event_key, run_id, reason=reason, idempotency_key=idem)),
        success="Запуск остановлен",
    )
    if ok:
        return redirect("events:card", event_key=event_key)
    return _card(request, event_key, status=400, stop_forms={str(run_id): form})


@require_GET
@require_perm(MANAGE)
def run_stats(request: HttpRequest, event_key: str, run_id: UUID) -> HttpResponse:
    """Сколько игроков открыли каждую находку запуска."""
    data, error = read(request, PROGRESSION, _client(lambda c: c.run_stats(event_key, run_id)))
    if error is not None:
        return _fail(request, error)
    stats = as_dict(data)
    raw_players = stats.get("players")
    players = raw_players if isinstance(raw_players, int) else 0
    finds = []
    for row in as_list(stats.get("finds")):
        raw = row.get("unlocked")
        unlocked = raw if isinstance(raw, int) else 0
        finds.append({**row, "share": round(100 * unlocked / players) if players else 0})
    return render(
        request,
        "admin_events/stats.html",
        {
            "title": f"Статистика: {event_key}",
            "event_key": event_key,
            "run_id": run_id,
            "players": players,
            "completed": stats.get("completed", 0),
            "finds": finds,
        },
    )
