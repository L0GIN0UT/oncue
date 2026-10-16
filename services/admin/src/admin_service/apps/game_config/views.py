"""Представления раздела «Режимы»: список, карточка с diff, редактор черновика, публикация.

Конфиги хранит game (D8): чтение и запись — только через его admin-API
(``core.game_client``). Права проверяются здесь (чтобы не показывать формы зря) и ещё раз
в game; каждое действие пишет ``AuditEntry`` и при успехе, и при отказе (``core.views.mutate``).
Тело конфига в аудит не пишется (до 64 КиБ): версия и ревизия однозначно указывают на него
в game.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from admin_service.apps.game_config import config_text
from admin_service.apps.game_config.forms import (
    MODE_KEY_RE,
    ArchiveForm,
    AssetRequestForm,
    EditorForm,
    ModeFilterForm,
    NewModeForm,
    PublishForm,
    RollbackForm,
)
from admin_service.core.game_client import GAME, GameAdminClient
from admin_service.core.media_client import MEDIA, MediaAdminClient
from admin_service.core.permissions import has_perm, require_perm
from admin_service.core.service_client import ServiceClient
from admin_service.core.views import mutate, read

VIEW, EDIT, PUBLISH = "config.modes.view", "config.modes.edit", "config.modes.publish"
_NOT_FOUND = 404
_ACTIONS = ("validate", "save")
PROFILE_MISSING = "assets.profile_missing"
# Предупреждения проверки конфига — человеческим текстом; неизвестный код показывается как есть.
_WARNING_TEXTS = {
    PROFILE_MISSING: "нет ни одного ассета под эти параметры — нажмите «Запросить ассеты»",
}
# Как параметры GET /admin/v1/profiles у media-pipeline: другое значение туда не отправляем.
_CLUE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PARAMS_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _warnings(result: Any) -> list[dict[str, str]]:
    """Предупреждения проверки для шаблона: код, секция конфига и пояснение."""
    return [
        {
            "code": str(w.get("code") or ""),
            "path": str(w.get("path") or ""),
            "text": _WARNING_TEXTS.get(str(w.get("code") or ""), ""),
        }
        for w in _list(_dict(result).get("warnings"))
    ]


# ---------------------------------------------------------------------------- список


@require_GET
@require_perm(VIEW)
def mode_list(request: HttpRequest) -> HttpResponse:
    """Список режимов: ключ, домен, статус, опубликованная версия, есть ли черновик."""
    form = ModeFilterForm(request.GET or None)
    filters: dict[str, str] = {}
    if form.is_bound and form.is_valid():
        filters = {k: v for k, v in form.cleaned_data.items() if v}
    rows, error = read(request, GAME, lambda c: GameAdminClient(c).mode_configs(**filters))
    modes = []
    for row in _list(rows):
        key = str(row.get("key") or "")
        url = reverse("game_config:mode", args=[key]) if MODE_KEY_RE.match(key) else None
        modes.append({**row, "admin_url": url})
    return render(
        request,
        "admin_game_config/mode_list.html",
        {
            "title": "Режимы",
            "form": form,
            "modes": modes,
            "error": error.message if error else None,
            "new_form": NewModeForm() if has_perm(request, EDIT) else None,
        },
    )


@require_GET
@require_perm(EDIT)
def mode_new(request: HttpRequest) -> HttpResponse:
    """Новый режим: проверить ключ и открыть пустой редактор (черновик — при сохранении)."""
    form = NewModeForm(request.GET)
    if not form.is_valid():
        errors = "; ".join(str(e) for e in form.errors.get("key", []))
        messages.error(request, f"Ключ режима: {errors}")
        return redirect("game_config:modes")
    return redirect("game_config:edit", key=form.cleaned_data["key"])


# ---------------------------------------------------------------------------- карточка


def _version_body(request: HttpRequest, key: str, version: int | None) -> tuple[Any, str | None]:
    if version is None:
        return None, None
    data, error = read(request, GAME, lambda c: GameAdminClient(c).version(key, version))
    if error is not None:
        return None, error.message
    return _dict(data).get("body"), None


def _detail_context(request: HttpRequest, key: str, **forms: Any) -> dict[str, Any]:
    detail, error = read(request, GAME, lambda c: GameAdminClient(c).mode_config(key))
    context: dict[str, Any] = {
        "title": f"Режим {key}",
        "key": key,
        "detail": None,
        "error": None,
        "not_found": False,
        "can_edit": has_perm(request, EDIT),
        "can_publish": has_perm(request, PUBLISH),
    }
    if error is not None:
        context["not_found"] = error.status == _NOT_FOUND
        context["error"] = None if context["not_found"] else error.message
        return context
    detail = _dict(detail)
    published = _int(detail.get("published_version"))
    versions = _list(detail.get("versions"))
    draft = _dict(detail.get("draft")) or None
    published_body, body_error = _version_body(request, key, published)

    shown = _int_param(request.GET.get("version"))
    shown_body, shown_error = (
        (published_body, body_error)
        if shown in (None, published)
        else _version_body(request, key, shown)
    )
    if shown is None:
        shown = published

    diff: list[config_text.DiffLine] = []
    if draft is not None and published_body is not None:
        diff = config_text.diff(
            published_body,
            draft.get("body"),
            old_label=f"v{published}",
            new_label=f"черновик r{draft.get('revision')}",
        )
    validation = _dict((draft or {}).get("validation"))
    draft_warnings = _warnings(validation)
    context.update(
        detail=detail,
        published=published,
        versions=versions,
        draft=draft,
        draft_valid=bool(validation.get("ok")),
        draft_errors=_list(validation.get("errors")),
        draft_warnings=draft_warnings,
        profile_missing=any(w["code"] == PROFILE_MISSING for w in draft_warnings),
        asset_form=forms.get("asset_form")
        or AssetRequestForm(initial={"draft_revision": (draft or {}).get("revision")}),
        **_asset_panel(request, key),
        diff=diff,
        draft_text=config_text.dump(draft.get("body"), "yaml") if draft else None,
        shown=shown,
        shown_text=config_text.dump(shown_body, "yaml") if shown_body is not None else None,
        body_error=shown_error,
        publish_form=forms.get("publish_form")
        or PublishForm(initial={"draft_revision": (draft or {}).get("revision")}),
        rollback_form=forms.get("rollback_form")
        or RollbackForm(
            versions=[v for v in (_int(x.get("version")) for x in versions) if v and v != published]
        ),
        archive_form=forms.get("archive_form") or ArchiveForm(),
    )
    return context


def _profiles_by_type(
    request: HttpRequest, clue_types: list[str]
) -> tuple[dict[tuple[str, str], dict[str, Any]], str | None]:
    """Профили media-pipeline по типам загадок: ``{(clue_type, params_hash): профиль}``.

    Один запрос на тип загадки, а не на каждый запрос ассетов: число типов ограничено
    плагинами, а запросов у режима со временем становится много.
    """
    found: dict[tuple[str, str], dict[str, Any]] = {}

    def profiles_of(clue_type: str) -> Callable[[ServiceClient], Any]:
        return lambda c: MediaAdminClient(c).profiles(clue_type=clue_type)

    for clue_type in clue_types:
        rows, error = read(request, MEDIA, profiles_of(clue_type))
        if error is not None:
            return found, error.message
        for row in _list(rows):
            found.setdefault((str(row.get("clue_type")), str(row.get("params_hash"))), row)
    return found, None


def _asset_panel(request: HttpRequest, key: str) -> dict[str, Any]:
    """Запросы ассетов режима (game) и статус их профилей (media-pipeline, ADR 0025).

    Недоступность media-pipeline не роняет карточку: панель показывает ошибку, а запросы —
    без статуса профиля.
    """
    rows, error = read(request, GAME, lambda c: GameAdminClient(c).asset_requests(key))
    requests = _list(rows)
    clue_types = sorted(
        {
            str(r.get("clue_type"))
            for r in requests
            if _CLUE_TYPE_RE.match(str(r.get("clue_type") or ""))
            and _PARAMS_HASH_RE.match(str(r.get("params_hash") or ""))
        }
    )
    profiles, media_error = _profiles_by_type(request, clue_types)
    return {
        "asset_requests": [
            {**r, "profile": profiles.get((str(r.get("clue_type")), str(r.get("params_hash"))))}
            for r in requests
        ],
        "asset_requests_error": error.message if error is not None else None,
        "profiles_error": media_error,
    }


def _int_param(raw: str | None) -> int | None:
    try:
        value = int(raw) if raw else None
    except ValueError:
        return None
    return value if value is not None and value >= 1 else None


@require_GET
@require_perm(VIEW)
def mode_detail(request: HttpRequest, key: str) -> HttpResponse:
    """Карточка режима: версии, тело выбранной версии, черновик и его diff с опубликованной."""
    context = _detail_context(request, key)
    status = _NOT_FOUND if context["not_found"] else 200
    return render(request, "admin_game_config/mode_detail.html", context, status=status)


# ---------------------------------------------------------------------------- редактор


def _validation_view(result: Any, text: str) -> dict[str, Any]:
    """Итог проверки для шаблона: ошибки с номерами строк текста и размеченный текст."""
    result = _dict(result)
    errors: list[dict[str, Any]] = []
    marked: set[int] = set()
    for item in _list(result.get("errors")):
        path = str(item.get("path") or "")
        line = config_text.line_of(text, path)
        if line is not None:
            marked.add(line)
        errors.append(
            {
                "path": path or "(корень)",
                "code": str(item.get("code") or ""),
                "message_key": str(item.get("message_key") or ""),
                "line": line,
            }
        )
    pool = _dict(result.get("pool"))
    return {
        "ok": result.get("ok") is True,
        "errors": errors,
        "warnings": _warnings(result),
        "playable_count": _int(pool.get("playable_count")),
        "lines": config_text.numbered(text, marked),
    }


def _initial_editor(request: HttpRequest, key: str, fmt: config_text.Format) -> dict[str, Any]:
    """Начальное состояние редактора: черновик, иначе опубликованная версия, иначе заготовка."""
    detail, error = read(request, GAME, lambda c: GameAdminClient(c).mode_config(key))
    if error is not None and error.status != _NOT_FOUND:
        return {"error": error.message, "form": None, "validation": None}
    detail = _dict(detail)
    draft = _dict(detail.get("draft")) or None
    if draft is not None:
        body, revision = draft.get("body"), _int(draft.get("revision"))
    elif error is None:
        body, body_error = _version_body(request, key, _int(detail.get("published_version")))
        if body_error is not None:
            return {"error": body_error, "form": None, "validation": None}
        revision = None
    else:  # режима ещё нет: заготовка с ключом и доменом, остальное — по схеме
        body, revision = {"key": key, "domain": key.split(".", 1)[0]}, None
    text = config_text.dump(body if isinstance(body, dict) else {}, fmt)
    form = EditorForm(initial={"fmt": fmt, "text": text, "base_revision": revision})
    validation = _validation_view(draft.get("validation"), text) if draft else None
    return {"error": None, "form": form, "validation": validation, "is_new": error is not None}


def _render_editor(
    request: HttpRequest, key: str, *, status: int = 200, **context: Any
) -> HttpResponse:
    return render(
        request,
        "admin_game_config/mode_edit.html",
        {"title": f"Черновик {key}", "key": key, **context},
        status=status,
    )


def _conflict_diff(request: HttpRequest, key: str, form: EditorForm) -> list[config_text.DiffLine]:
    """Если черновик за это время изменили — diff «текущий черновик → ваш текст»."""
    detail, error = read(request, GAME, lambda c: GameAdminClient(c).mode_config(key))
    draft = _dict(_dict(detail).get("draft"))
    if (
        error is not None
        or not draft
        or draft.get("revision") == form.cleaned_data.get("base_revision")
    ):
        return []
    return config_text.diff(
        draft.get("body"),
        form.body,
        old_label=f"текущий черновик r{draft.get('revision')}",
        new_label="ваш текст",
    )


@require_http_methods(["GET", "POST"])
@require_perm(EDIT)
def mode_edit(request: HttpRequest, key: str) -> HttpResponse:
    """Редактор черновика: «Проверить» (validate, без записи) и «Сохранить» (с причиной)."""
    if request.method == "GET":
        fmt: config_text.Format = "json" if request.GET.get("fmt") == "json" else "yaml"
        return _render_editor(request, key, **_initial_editor(request, key, fmt))

    form = EditorForm(request.POST)
    action = request.POST.get("action")
    if action not in _ACTIONS or not form.is_valid() or form.body is None:
        return _render_editor(request, key, form=form, status=400)
    text: str = form.cleaned_data["text"]
    body = form.body

    if action == "validate":
        result, error = read(request, GAME, lambda c: GameAdminClient(c).validate(body))
        if error is not None:
            return _render_editor(request, key, form=form, error=error.message)
        return _render_editor(request, key, form=form, validation=_validation_view(result, text))

    return _save_draft(request, key, form)


def _save_draft(request: HttpRequest, key: str, form: EditorForm) -> HttpResponse:
    """Сохранить черновик (с причиной); при отказе текст остаётся в форме."""
    body = form.body
    if body is None or not form.require_reason():
        return _render_editor(request, key, form=form, status=400)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    base_revision = form.cleaned_data.get("base_revision")
    ok, result = mutate(
        request,
        service=GAME,
        action="game.save_mode_draft",
        target_type="mode_config",
        target_id=key,
        reason=reason,
        idempotency_key=idem,
        before={"revision": base_revision},
        audit_fields=("revision",),
        call=lambda c: GameAdminClient(c).save_draft(
            key, body=body, base_revision=base_revision, reason=reason, idempotency_key=idem
        ),
        success="Черновик сохранён",
    )
    if not ok:
        return _render_editor(
            request, key, form=form, conflict_diff=_conflict_diff(request, key, form)
        )
    if not _dict(_dict(result).get("validation")).get("ok"):
        messages.warning(request, "Черновик не прошёл проверку — опубликовать его нельзя")
    return redirect("game_config:edit", key=key)


# ---------------------------------------------------------------------------- публикация


def _current(request: HttpRequest, key: str) -> dict[str, Any] | None:
    """Состояние «до» для аудита (ошибка чтения не мешает действию: решает game)."""
    detail, _ = read(request, GAME, lambda c: GameAdminClient(c).mode_config(key))
    if not isinstance(detail, dict):
        return None
    draft = _dict(detail.get("draft"))
    return {
        "status": detail.get("status"),
        "published_version": detail.get("published_version"),
        "draft_revision": draft.get("revision") if draft else None,
    }


def _invalid(request: HttpRequest, key: str, **forms: Any) -> HttpResponse:
    context = _detail_context(request, key, **forms)
    return render(request, "admin_game_config/mode_detail.html", context, status=400)


_VERSION_FIELDS = ("version", "body_hash", "rolled_back_from")


@require_POST
@require_perm(PUBLISH)
def mode_publish(request: HttpRequest, key: str) -> HttpResponse:
    """Опубликовать черновик ревизии ``draft_revision`` → новая версия."""
    form = PublishForm(request.POST)
    if not form.is_valid():
        return _invalid(request, key, publish_form=form)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    revision = form.cleaned_data["draft_revision"]
    mutate(
        request,
        service=GAME,
        action="game.publish_mode",
        target_type="mode_config",
        target_id=key,
        reason=reason,
        idempotency_key=idem,
        before=_current(request, key),
        audit_fields=_VERSION_FIELDS,
        call=lambda c: GameAdminClient(c).publish(
            key, draft_revision=revision, reason=reason, idempotency_key=idem
        ),
        success="Черновик опубликован",
    )
    return redirect("game_config:mode", key=key)


@require_POST
@require_perm(PUBLISH)
def mode_rollback(request: HttpRequest, key: str) -> HttpResponse:
    """Откат: копия выбранной версии публикуется как новая."""
    form = RollbackForm(request.POST)
    if not form.is_valid():
        return _invalid(request, key, rollback_form=form)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    to_version = form.cleaned_data["to_version"]
    mutate(
        request,
        service=GAME,
        action="game.rollback_mode",
        target_type="mode_config",
        target_id=key,
        reason=reason,
        idempotency_key=idem,
        before=_current(request, key),
        audit_fields=_VERSION_FIELDS,
        call=lambda c: GameAdminClient(c).rollback(
            key, to_version=to_version, reason=reason, idempotency_key=idem
        ),
        success=f"Версия v{to_version} опубликована заново",
    )
    return redirect("game_config:mode", key=key)


@require_POST
@require_perm(PUBLISH)
def mode_archive(request: HttpRequest, key: str) -> HttpResponse:
    """Убрать режим из каталога (начатые сессии доигрываются)."""
    form = ArchiveForm(request.POST)
    if not form.is_valid():
        return _invalid(request, key, archive_form=form)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    mutate(
        request,
        service=GAME,
        action="game.archive_mode",
        target_type="mode_config",
        target_id=key,
        reason=reason,
        idempotency_key=idem,
        before=_current(request, key),
        audit_fields=("status", "published_version"),
        call=lambda c: GameAdminClient(c).archive(key, reason=reason, idempotency_key=idem),
        success="Режим отправлен в архив",
    )
    return redirect("game_config:mode", key=key)


# ---------------------------------------------------------------------------- ассеты


@require_POST
@require_perm(PUBLISH)
def mode_request_assets(request: HttpRequest, key: str) -> HttpResponse:
    """«Запросить ассеты» под загадку (и ревил) черновика: game публикует событие (D8).

    Право — ``config.modes.publish`` (§2.7 плана этапа 3a): запрос запускает генерацию
    в media-pipeline. 409 ``game.assets_already_available`` — понятное сообщение и аудит отказа.
    """
    form = AssetRequestForm(request.POST)
    if not form.is_valid():
        return _invalid(request, key, asset_form=form)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    revision = form.cleaned_data["draft_revision"]
    mutate(
        request,
        service=GAME,
        action="game.request_assets",
        target_type="mode_config",
        target_id=key,
        reason=reason,
        idempotency_key=idem,
        before={"draft_revision": revision},
        audit_fields=("request_id", "clue_type", "params_hash", "status"),
        call=lambda c: GameAdminClient(c).request_assets(
            key, draft_revision=revision, reason=reason, idempotency_key=idem
        ),
        success="Запрос ассетов отправлен: media-pipeline сгенерирует наборы порциями",
    )
    return redirect("game_config:mode", key=key)
