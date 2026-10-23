"""Представления раздела «Рейтинг»: ladder-ы, версии, редактор, публикация, откат, сезоны.

Конфиги и сезоны хранит rating (ADR 0037, 0039): чтение и запись — только через его
admin-API (``core.rating_client``). Права проверяются здесь (кнопки без права не показываются,
действие без права — 403) и ещё раз в rating: ladder-ы — ``rating.ladders.publish``, сезоны и
пересборка — ``rating.seasons.manage``. Каждая мутация пишет ``AuditEntry`` и при успехе, и при
отказе (``core.views.mutate``). Тело конфига в аудит не пишется — только версия и статус.
Проверку конфига выполняет rating (схема ``ladder.v1.json`` + инварианты домена): «Сохранить»
— это и проверка, ошибки приходят путями и показываются с номерами строк.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC
from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from admin_service.apps.game_config import config_text
from admin_service.apps.game_config.forms import EditorForm
from admin_service.apps.rating import ladder_text
from admin_service.apps.rating.forms import (
    PublishForm,
    RebuildForm,
    RollbackForm,
    SeasonCloseForm,
    SeasonCreateForm,
)
from admin_service.core.permissions import has_perm, require_perm
from admin_service.core.rating_client import RATING, RatingAdminClient
from admin_service.core.service_client import ServiceClient, ServiceError
from admin_service.core.views import mutate, read

LADDERS, SEASONS = "rating.ladders.publish", "rating.seasons.manage"
_NOT_FOUND = 404
_INVALID_STATUSES = (400, 422)
_DRAFT, _PUBLISHED = "draft", "published"
_VERSION_FIELDS = ("version", "status")
_SEASON_FIELDS = ("season_id", "number", "status")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _int_param(raw: str | None) -> int | None:
    try:
        value = int(raw) if raw else None
    except ValueError:
        return None
    return value if value is not None and value >= 1 else None


# ---------------------------------------------------------------------------- список


@require_GET
@require_perm(LADDERS)
def ladder_list(request: HttpRequest) -> HttpResponse:
    """Ladder-ы: действующая и черновая версии, текущий сезон."""
    data, error = read(request, RATING, lambda c: RatingAdminClient(c).ladders())
    return render(
        request,
        "admin_rating/ladder_list.html",
        {
            "title": "Рейтинг: ladder-ы",
            "ladders": _list(_dict(data).get("items")),
            "error": error.message if error else None,
        },
    )


# ---------------------------------------------------------------------------- карточка


def _version_config(request: HttpRequest, key: str, version: int | None) -> tuple[Any, str | None]:
    if version is None:
        return None, None
    data, error = read(request, RATING, lambda c: RatingAdminClient(c).version(key, version))
    if error is not None:
        return None, error.message
    return _dict(data).get("config"), None


def _detail_context(request: HttpRequest, key: str, **forms: Any) -> dict[str, Any]:
    versions_data, error = read(request, RATING, lambda c: RatingAdminClient(c).versions(key))
    context: dict[str, Any] = {
        "title": f"Ladder {key}",
        "key": key,
        "error": None,
        "not_found": False,
        "can_ladders": has_perm(request, LADDERS),
        "can_seasons": has_perm(request, SEASONS),
    }
    if error is not None:
        context["not_found"] = error.status == _NOT_FOUND
        context["error"] = None if context["not_found"] else error.message
        return context
    versions = _list(_dict(versions_data).get("items"))
    by_status: dict[str, int | None] = {_DRAFT: None, _PUBLISHED: None}
    for card in versions:  # новые первыми: берём самую свежую каждого статуса
        status = str(card.get("status"))
        if status in by_status and by_status[status] is None:
            by_status[status] = _int(card.get("version"))
    draft, published = by_status[_DRAFT], by_status[_PUBLISHED]

    # Diff: по умолчанию черновик против действующей версии; ?from=&to= — любые две.
    old_v = _int_param(request.GET.get("from")) or published
    new_v = _int_param(request.GET.get("to")) or draft
    diff: list[config_text.DiffLine] = []
    diff_error = None
    if old_v is not None and new_v is not None and old_v != new_v:
        old_cfg, e1 = _version_config(request, key, old_v)
        new_cfg, e2 = _version_config(request, key, new_v)
        diff_error = e1 or e2
        if diff_error is None:
            diff = config_text.diff(old_cfg, new_cfg, old_label=f"v{old_v}", new_label=f"v{new_v}")

    shown = _int_param(request.GET.get("version"))
    shown_cfg, shown_error = _version_config(request, key, shown)
    ladders, _ = read(request, RATING, lambda c: RatingAdminClient(c).ladders())
    card = next((x for x in _list(_dict(ladders).get("items")) if x.get("key") == key), {})
    rollback_versions = [
        v
        for v in (_int(x.get("version")) for x in versions if x.get("status") != _DRAFT)
        if v and v != published
    ]
    context.update(
        versions=versions,
        draft=draft,
        published=published,
        season_id=card.get("season_id"),
        diff=diff,
        diff_error=diff_error,
        diff_from=old_v,
        diff_to=new_v,
        shown=shown,
        shown_text=config_text.dump(shown_cfg, "yaml") if shown_cfg is not None else None,
        shown_error=shown_error,
        publish_form=forms.get("publish_form")
        or PublishForm(initial={"version": draft} if draft else None),
        rollback_form=forms.get("rollback_form") or RollbackForm(versions=rollback_versions),
        season_form=forms.get("season_form") or SeasonCreateForm(),
        rebuild_form=forms.get("rebuild_form") or RebuildForm(),
    )
    return context


@require_GET
@require_perm(LADDERS)
def ladder_detail(request: HttpRequest, key: str) -> HttpResponse:
    """Карточка ladder-а: версии, тело версии, diff, публикация, откат, сезон."""
    context = _detail_context(request, key)
    status = _NOT_FOUND if context["not_found"] else 200
    return render(request, "admin_rating/ladder_detail.html", context, status=status)


def _invalid(request: HttpRequest, key: str, **forms: Any) -> HttpResponse:
    context = _detail_context(request, key, **forms)
    return render(request, "admin_rating/ladder_detail.html", context, status=400)


# ---------------------------------------------------------------------------- редактор


def _capturing(
    call: Callable[[RatingAdminClient], Any], sink: list[ServiceError]
) -> Callable[[ServiceClient], Any]:
    """Вызов для ``mutate`` с сохранением ``ServiceError``: редактору нужны пути ошибок."""

    def run(client: ServiceClient) -> Any:
        try:
            return call(RatingAdminClient(client))
        except ServiceError as exc:
            sink.append(exc)
            raise

    return run


def _initial_editor(request: HttpRequest, key: str, fmt: config_text.Format) -> dict[str, Any]:
    """Начальный текст: черновик, иначе действующая версия."""
    data, error = read(request, RATING, lambda c: RatingAdminClient(c).versions(key))
    if error is not None:
        return {"error": error.message, "form": None, "not_found": error.status == _NOT_FOUND}
    cards = _list(_dict(data).get("items"))
    pick = next((c for c in cards if c.get("status") == _DRAFT), None) or next(
        (c for c in cards if c.get("status") == _PUBLISHED), None
    )
    version = _int(pick.get("version")) if pick else None
    config, config_error = _version_config(request, key, version)
    if config_error is not None:
        return {"error": config_error, "form": None}
    text = config_text.dump(config if isinstance(config, dict) else {}, fmt)
    return {
        "error": None,
        "form": EditorForm(initial={"fmt": fmt, "text": text}),
        "based_on": version,
        "based_status": pick.get("status") if pick else None,
    }


def _render_editor(
    request: HttpRequest, key: str, *, status: int = 200, **context: Any
) -> HttpResponse:
    return render(
        request,
        "admin_rating/ladder_edit.html",
        {"title": f"Черновик ladder-а {key}", "key": key, **context},
        status=status,
    )


@require_http_methods(["GET", "POST"])
@require_perm(LADDERS)
def ladder_edit(request: HttpRequest, key: str) -> HttpResponse:
    """Редактор конфига (YAML/JSON): сохранение черновика новой версией.

    Проверку делает rating: 422 ``rating.ladder_invalid`` — таблица ошибок с номерами строк,
    текст остаётся в форме.
    """
    if request.method == "GET":
        fmt: config_text.Format = "json" if request.GET.get("fmt") == "json" else "yaml"
        initial = _initial_editor(request, key, fmt)
        status = _NOT_FOUND if initial.get("not_found") else 200
        return _render_editor(request, key, status=status, **initial)

    form = EditorForm(request.POST)
    if not form.is_valid() or form.body is None or not form.require_reason():
        return _render_editor(request, key, form=form, status=400)
    body = form.body
    text: str = form.cleaned_data["text"]
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    errors: list[ServiceError] = []
    ok, _ = mutate(
        request,
        service=RATING,
        action="rating.save_ladder_draft",
        target_type="ladder",
        target_id=key,
        reason=reason,
        idempotency_key=idem,
        audit_fields=_VERSION_FIELDS,
        call=_capturing(
            lambda c: c.save_draft(key, config=body, reason=reason, idempotency_key=idem), errors
        ),
        success="Черновик сохранён",
    )
    if ok:
        return redirect("rating:ladder", key=key)
    context: dict[str, Any] = {}
    if errors and errors[0].status in _INVALID_STATUSES:
        rows = ladder_text.error_rows(text, details=errors[0].details, errors=errors[0].errors)
        marked = {r["line"] for r in rows if r["line"] is not None}
        context = {"field_errors": rows, "lines": config_text.numbered(text, marked)}
    return _render_editor(request, key, form=form, **context)


# ---------------------------------------------------------------------------- версии


@require_POST
@require_perm(LADDERS)
def ladder_publish(request: HttpRequest, key: str) -> HttpResponse:
    """Опубликовать версию-черновик → ``LadderPublished``."""
    form = PublishForm(request.POST)
    if not form.is_valid():
        return _invalid(request, key, publish_form=form)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    version = form.cleaned_data["version"]
    mutate(
        request,
        service=RATING,
        action="rating.publish_ladder",
        target_type="ladder",
        target_id=key,
        reason=reason,
        idempotency_key=idem,
        before={"draft_version": version},
        audit_fields=_VERSION_FIELDS,
        call=lambda c: RatingAdminClient(c).publish(
            key, version=version, reason=reason, idempotency_key=idem
        ),
        success=f"Версия v{version} опубликована",
    )
    return redirect("rating:ladder", key=key)


@require_POST
@require_perm(LADDERS)
def ladder_rollback(request: HttpRequest, key: str) -> HttpResponse:
    """Откат: копия выбранной версии публикуется как новая."""
    form = RollbackForm(request.POST)
    if not form.is_valid():
        return _invalid(request, key, rollback_form=form)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    to_version = form.cleaned_data["to_version"]
    mutate(
        request,
        service=RATING,
        action="rating.rollback_ladder",
        target_type="ladder",
        target_id=key,
        reason=reason,
        idempotency_key=idem,
        before={"to_version": to_version},
        audit_fields=_VERSION_FIELDS,
        call=lambda c: RatingAdminClient(c).rollback(
            key, to_version=to_version, reason=reason, idempotency_key=idem
        ),
        success=f"Версия v{to_version} опубликована заново",
    )
    return redirect("rating:ladder", key=key)


# ---------------------------------------------------------------------------- сезоны


@require_POST
@require_perm(SEASONS)
def season_create(request: HttpRequest, key: str) -> HttpResponse:
    """Создать сезон ladder-а."""
    form = SeasonCreateForm(request.POST)
    if not form.is_valid():
        return _invalid(request, key, season_form=form)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    starts_at = form.cleaned_data.get("starts_at")
    if starts_at is not None and starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=UTC)
    mutate(
        request,
        service=RATING,
        action="rating.create_season",
        target_type="ladder",
        target_id=key,
        reason=reason,
        idempotency_key=idem,
        audit_fields=_SEASON_FIELDS,
        call=lambda c: RatingAdminClient(c).create_season(
            key, starts_at=starts_at, reason=reason, idempotency_key=idem
        ),
        success="Сезон создан",
    )
    return redirect("rating:ladder", key=key)


@require_http_methods(["GET", "POST"])
@require_perm(SEASONS)
def season_close(request: HttpRequest, key: str, season_id: UUID) -> HttpResponse:
    """Закрыть сезон: страница подтверждения с причиной и отметкой «понимаю»."""
    form = SeasonCloseForm(request.POST if request.method == "POST" else None)
    if request.method == "GET" or not form.is_valid():
        return render(
            request,
            "admin_rating/season_close.html",
            {
                "title": f"Закрыть сезон ladder-а {key}",
                "key": key,
                "season_id": season_id,
                "form": form,
            },
            status=400 if form.is_bound else 200,
        )
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    mutate(
        request,
        service=RATING,
        action="rating.close_season",
        target_type="season",
        target_id=str(season_id),
        reason=reason,
        idempotency_key=idem,
        audit_fields=_SEASON_FIELDS,
        call=lambda c: RatingAdminClient(c).close_season(
            season_id, reason=reason, idempotency_key=idem
        ),
        success="Сезон закрыт, следующий откроется автоматически",
    )
    return redirect("rating:ladder", key=key)


@require_POST
@require_perm(SEASONS)
def leaderboard_rebuild(request: HttpRequest, key: str) -> HttpResponse:
    """Пересобрать лидерборд текущего сезона из Postgres (задача выполняется асинхронно)."""
    form = RebuildForm(request.POST)
    if not form.is_valid():
        return _invalid(request, key, rebuild_form=form)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    mutate(
        request,
        service=RATING,
        action="rating.rebuild_leaderboard",
        target_type="ladder",
        target_id=key,
        reason=reason,
        idempotency_key=idem,
        audit_fields=("season_id", "status"),
        call=lambda c: RatingAdminClient(c).rebuild_leaderboard(
            key, reason=reason, idempotency_key=idem
        ),
        success="Пересборка лидерборда принята",
    )
    return redirect("rating:ladder", key=key)
