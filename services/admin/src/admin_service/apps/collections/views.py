"""Представления раздела «Сборники»: список, карточка с составом, модерация, кураторские (D14).

Сборники хранит collections: чтение и запись — только через его admin-API
(``core.collections_client``). Права проверяются здесь (чтобы не показывать формы зря) и ещё
раз в collections; каждое действие пишет ``AuditEntry`` и при успехе, и при отказе
(``core.views.mutate``). В аудит — версии, статусы и флаги, но не тексты: название, описание
и эмодзи однозначно восстанавливаются по версии сборника в collections.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from admin_service.apps.collections import collection_text
from admin_service.apps.collections.forms import (
    CollectionFilterForm,
    CuratedEditorForm,
    FeaturedForm,
    HideForm,
    NewCuratedForm,
    RestrictionForm,
)
from admin_service.apps.game_config import config_text
from admin_service.core.collections_client import COLLECTIONS, CollectionsAdminClient
from admin_service.core.permissions import has_perm, require_perm
from admin_service.core.service_client import ServiceClient, ServiceError
from admin_service.core.views import mutate, read

VIEW, CURATE = "collections.view", "collections.curate"
UNPUBLISH, RESTRICT = "collections.unpublish", "collections.restrict_author"
_NOT_FOUND = 404
_CURATED = "curated"
# Ошибки, у которых есть пути полей: их показываем таблицей с номерами строк текста.
_FIELD_ERRORS = frozenset(
    {"collections.publish_invalid", "collections.draft_invalid", "request.validation_failed"}
)
_VERSION_CONFLICT = "collections.version_conflict"
_AUTHOR_LABELS = {"curator": "Guessverse", "deleted": "автор удалён"}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _author_label(author: Any) -> str:
    author = _dict(author)
    kind = str(author.get("kind") or "")
    return _AUTHOR_LABELS.get(kind) or str(author.get("nickname") or author.get("user_id") or "—")


def _row(row: dict[str, Any]) -> dict[str, Any]:
    collection_id = _uuid(row.get("collection_id"))
    return {
        **row,
        "author_label": _author_label(row.get("author")),
        "admin_url": reverse("collections:detail", kwargs={"collection_id": collection_id})
        if collection_id
        else None,
    }


def _detail(request: HttpRequest, collection_id: UUID) -> tuple[Any, ServiceError | None]:
    return read(request, COLLECTIONS, lambda c: CollectionsAdminClient(c).collection(collection_id))


# ---------------------------------------------------------------------------- список


@require_GET
@require_perm(VIEW)
def collection_list(request: HttpRequest) -> HttpResponse:
    """Сборники (включая черновики и скрытые): поиск по названию и нику автора."""
    form = CollectionFilterForm(request.GET or None)
    filters: dict[str, Any] = {}  # Any: значения формы (строки), ключи — параметры клиента
    if form.is_bound and form.is_valid():
        filters = {k: v for k, v in form.cleaned_data.items() if v}
    page, error = read(
        request, COLLECTIONS, lambda c: CollectionsAdminClient(c).collections(**filters)
    )
    page = _dict(page)
    next_cursor = page.get("next_cursor")
    params = {k: v for k, v in filters.items() if k != "cursor"}
    return render(
        request,
        "admin_collections/collection_list.html",
        {
            "title": "Сборники",
            "form": form,
            "collections": [_row(r) for r in _list(page.get("items"))],
            "next_url": f"?{urlencode({**params, 'cursor': next_cursor})}"
            if isinstance(next_cursor, str) and next_cursor
            else None,
            "error": error.message if error else None,
            "new_form": NewCuratedForm() if has_perm(request, CURATE) else None,
        },
        status=400 if form.is_bound and form.errors else 200,
    )


# ---------------------------------------------------------------------------- карточка


def _items_view(rows: Any) -> list[dict[str, Any]]:
    """Состав для шаблона: ссылка на единицу каталога, если id — корректный UUID."""
    result = []
    for row in sorted(_list(rows), key=lambda r: _int(r.get("position")) or 0):
        item_id = _uuid(row.get("media_item_id"))
        result.append(
            {
                **row,
                "emoji": _dict(row.get("custom")).get("emoji"),
                "catalog_url": reverse("catalog:item", kwargs={"item_id": item_id})
                if item_id
                else None,
            }
        )
    return result


def _detail_context(request: HttpRequest, collection_id: UUID, **forms: Any) -> dict[str, Any]:
    detail, error = _detail(request, collection_id)
    context: dict[str, Any] = {
        "title": "Сборник",
        "collection_id": collection_id,
        "detail": None,
        "error": None,
        "not_found": False,
    }
    if error is not None:
        context["not_found"] = error.status == _NOT_FOUND
        context["error"] = error.message
        return context
    detail = _dict(detail)
    status = str(detail.get("status") or "")
    author = _dict(detail.get("author"))
    author_id = _uuid(author.get("user_id")) if author.get("kind") == "user" else None
    context.update(
        title=f"Сборник «{detail.get('title') or collection_id}»",
        detail=detail,
        author_label=_author_label(author),
        author_id=author_id,
        items=_items_view(detail.get("items")),
        can_hide=has_perm(request, UNPUBLISH) and status == "published",
        can_unhide=has_perm(request, UNPUBLISH) and status == "hidden",
        can_feature=has_perm(request, CURATE) and status == "published",
        can_edit=has_perm(request, CURATE) and detail.get("kind") == _CURATED,
        can_restrict=has_perm(request, RESTRICT) and author_id is not None,
        hide_form=forms.get("hide_form") or HideForm(),
        featured_form=forms.get("featured_form")
        or FeaturedForm(
            initial={
                "featured": bool(detail.get("featured")),
                "rank": _int(detail.get("featured_rank")),
            }
        ),
        restriction_form=forms.get("restriction_form")
        or RestrictionForm(initial={"restricted": "0" if detail.get("author_restricted") else "1"}),
    )
    return context


@require_GET
@require_perm(VIEW)
def collection_detail(request: HttpRequest, collection_id: UUID) -> HttpResponse:
    """Карточка: статус, автор, флаги, состав со своими эмодзи и формы действий."""
    context = _detail_context(request, collection_id)
    status = _NOT_FOUND if context["not_found"] else 200
    return render(request, "admin_collections/collection_detail.html", context, status=status)


def _invalid(request: HttpRequest, collection_id: UUID, **forms: Any) -> HttpResponse:
    context = _detail_context(request, collection_id, **forms)
    return render(request, "admin_collections/collection_detail.html", context, status=400)


# ---------------------------------------------------------------------------- модерация


def _set_hidden(request: HttpRequest, collection_id: UUID, *, hidden: bool) -> HttpResponse:
    form = HideForm(request.POST)
    if not form.is_valid():
        return _invalid(request, collection_id, hide_form=form)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    current, _ = _detail(request, collection_id)  # «до» для аудита; ошибка чтения не мешает
    mutate(
        request,
        service=COLLECTIONS,
        action="collections.hide" if hidden else "collections.unhide",
        target_type="collection",
        target_id=str(collection_id),
        reason=reason,
        idempotency_key=idem,
        before={"status": _dict(current).get("status")} if current else None,
        audit_fields=("status", "version"),
        call=lambda c: CollectionsAdminClient(c).set_hidden(
            collection_id, hidden=hidden, reason=reason, idempotency_key=idem
        ),
        success="Сборник скрыт: пропал из каталога и не стартует в game"
        if hidden
        else "Сборник снова виден в каталоге",
    )
    return redirect("collections:detail", collection_id=collection_id)


@require_POST
@require_perm(UNPUBLISH)
def collection_hide(request: HttpRequest, collection_id: UUID) -> HttpResponse:
    """Скрыть сборник (из каталога пропадает, старт сессии в game — 404)."""
    return _set_hidden(request, collection_id, hidden=True)


@require_POST
@require_perm(UNPUBLISH)
def collection_unhide(request: HttpRequest, collection_id: UUID) -> HttpResponse:
    """Показать скрытый сборник."""
    return _set_hidden(request, collection_id, hidden=False)


@require_POST
@require_perm(CURATE)
def collection_featured(request: HttpRequest, collection_id: UUID) -> HttpResponse:
    """Отметка «рекомендуемое» и порядок в блоке (кураторские и UGC, D14)."""
    form = FeaturedForm(request.POST)
    if not form.is_valid():
        return _invalid(request, collection_id, featured_form=form)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    featured: bool = form.cleaned_data["featured"]
    rank: int | None = form.cleaned_data.get("rank")
    current, _ = _detail(request, collection_id)
    before = (
        {"featured": current.get("featured"), "featured_rank": current.get("featured_rank")}
        if isinstance(current, dict)
        else None
    )
    mutate(
        request,
        service=COLLECTIONS,
        action="collections.set_featured",
        target_type="collection",
        target_id=str(collection_id),
        reason=reason,
        idempotency_key=idem,
        before=before,
        audit_fields=("featured", "featured_rank"),
        call=lambda c: CollectionsAdminClient(c).set_featured(
            collection_id, featured=featured, rank=rank, reason=reason, idempotency_key=idem
        ),
        success="Сборник в блоке «Рекомендуемое»" if featured else "Отметка «рекомендуемое» снята",
    )
    return redirect("collections:detail", collection_id=collection_id)


@require_POST
@require_perm(RESTRICT)
def author_restriction(request: HttpRequest, collection_id: UUID) -> HttpResponse:
    """Ограничить автора сборника (или снять ограничение).

    Автор берётся из карточки сборника на сервере, а не из формы: так нельзя подставить
    чужой ``user_id``, а в аудит попадает прежнее состояние ограничения.
    """
    form = RestrictionForm(request.POST)
    if not form.is_valid():
        return _invalid(request, collection_id, restriction_form=form)
    current, error = _detail(request, collection_id)
    author = _dict(_dict(current).get("author"))
    user_id = _uuid(author.get("user_id")) if author.get("kind") == "user" else None
    if user_id is None:
        messages.error(request, error.message if error else "У сборника нет автора-пользователя")
        return redirect("collections:detail", collection_id=collection_id)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    restricted: bool = form.cleaned_data["restricted"]
    mutate(
        request,
        service=COLLECTIONS,
        action="collections.restrict_author" if restricted else "collections.unrestrict_author",
        target_type="user",
        target_id=str(user_id),
        reason=reason,
        idempotency_key=idem,
        before={"restricted": bool(_dict(current).get("author_restricted"))},
        audit_fields=("restricted",),
        call=lambda c: CollectionsAdminClient(c).set_restriction(
            user_id, restricted=restricted, reason=reason, idempotency_key=idem
        ),
        success="Автор ограничен: создавать и править сборники он не может"
        if restricted
        else "Ограничение автора снято",
    )
    return redirect("collections:detail", collection_id=collection_id)


# ---------------------------------------------------------------------------- редактор


def _render_editor(request: HttpRequest, *, status: int = 200, **context: Any) -> HttpResponse:
    return render(request, "admin_collections/curated_edit.html", context, status=status)


def _capturing(
    call: Callable[[CollectionsAdminClient], Any], sink: list[ServiceError]
) -> Callable[[ServiceClient], Any]:
    """Вызов для ``mutate`` с сохранением ``ServiceError``: редактору нужны пути ошибок."""

    def run(client: ServiceClient) -> Any:
        try:
            return call(CollectionsAdminClient(client))
        except ServiceError as exc:
            sink.append(exc)
            raise

    return run


def _refusal_context(
    request: HttpRequest,
    form: CuratedEditorForm,
    error: ServiceError | None,
    collection_id: UUID | None,
) -> dict[str, Any]:
    """Отказ сервиса для редактора: ошибки с номерами строк или diff при конфликте версий."""
    text: str = form.cleaned_data.get("text") or ""
    if error is None:
        return {}
    if error.code in _FIELD_ERRORS:
        rows = collection_text.error_rows(text, details=error.details, errors=error.errors)
        marked = {r["line"] for r in rows if r["line"] is not None}
        return {"field_errors": rows, "lines": config_text.numbered(text, marked)}
    if error.code == _VERSION_CONFLICT and collection_id is not None and form.body is not None:
        current, read_error = _detail(request, collection_id)
        if read_error is None and isinstance(current, dict):
            new_body = {"domain": current.get("domain"), **form.body}
            return {
                "conflict_diff": config_text.diff(
                    collection_text.body_of(current),
                    new_body,
                    old_label=f"текущая версия v{current.get('version')}",
                    new_label="ваш текст",
                ),
            }
    return {}


@require_http_methods(["GET", "POST"])
@require_perm(CURATE)
def curated_new(request: HttpRequest) -> HttpResponse:
    """Новый кураторский сборник: YAML-заготовка → ``POST /admin/v1/collections`` (публикация)."""
    context: dict[str, Any] = {"title": "Новый кураторский сборник", "collection_id": None}
    if request.method == "GET":
        domain_form = NewCuratedForm(request.GET or None)
        domain = domain_form.cleaned_data["domain"] if domain_form.is_valid() else "film"
        form = CuratedEditorForm(initial={"text": collection_text.template(domain)})
        return _render_editor(request, form=form, **context)

    form = CuratedEditorForm(request.POST)
    if not form.is_valid() or form.body is None:
        return _render_editor(request, form=form, status=400, **context)
    body = form.body
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    errors: list[ServiceError] = []
    ok, result = mutate(
        request,
        service=COLLECTIONS,
        action="collections.create_curated",
        target_type="collection",
        target_id="new",
        reason=reason,
        idempotency_key=idem,
        audit_fields=("collection_id", "version", "status", "items_count"),
        call=_capturing(
            lambda c: c.create_curated(body, reason=reason, idempotency_key=idem), errors
        ),
        success="Кураторский сборник опубликован",
    )
    created = _uuid(_dict(result).get("collection_id")) if ok else None
    if created is not None:
        return redirect("collections:detail", collection_id=created)
    if ok:  # ответ без id — сборник создан, но открыть его нечем: к списку
        return redirect("collections:list")
    refusal = _refusal_context(request, form, errors[0] if errors else None, None)
    return _render_editor(request, form=form, **context, **refusal)


@require_http_methods(["GET", "POST"])
@require_perm(CURATE)
def curated_edit(request: HttpRequest, collection_id: UUID) -> HttpResponse:
    """Новая версия кураторского сборника: YAML → ``PUT`` с ``base_version``.

    409 ``collections.version_conflict`` — текст остаётся в форме, показывается diff с
    текущей версией; ошибки публикации — таблицей с номерами строк.
    """
    current, error = _detail(request, collection_id)
    context: dict[str, Any] = {
        "title": "Кураторский сборник",
        "collection_id": collection_id,
    }
    if error is not None:
        status = _NOT_FOUND if error.status == _NOT_FOUND else 200
        return _render_editor(request, form=None, error=error.message, status=status, **context)
    current = _dict(current)
    if current.get("kind") != _CURATED:
        messages.error(request, "Это пользовательский сборник: его правит только автор")
        return redirect("collections:detail", collection_id=collection_id)
    context["title"] = f"Кураторский сборник «{current.get('title') or collection_id}»"

    if request.method == "GET":
        form = CuratedEditorForm(
            initial={
                "text": collection_text.dump(current),
                "base_version": _int(current.get("version")),
            }
        )
        return _render_editor(request, form=form, **context)
    return _save_version(request, collection_id, current, context)


def _save_version(
    request: HttpRequest, collection_id: UUID, current: dict[str, Any], context: dict[str, Any]
) -> HttpResponse:
    """Отправить новую версию; при отказе текст остаётся в форме вместе с ошибками или diff."""
    form = CuratedEditorForm(request.POST)
    if not form.is_valid() or form.body is None:
        return _render_editor(request, form=form, status=400, **context)
    body = dict(form.body)
    domain = body.pop("domain", current.get("domain"))
    if domain != current.get("domain"):
        form.add_error("text", "домен сборника менять нельзя")
        return _render_editor(request, form=form, status=400, **context)
    base_version: int | None = form.cleaned_data.get("base_version")
    if base_version is None:
        form.add_error(None, "нет версии, от которой правили — откройте редактор заново")
        return _render_editor(request, form=form, status=400, **context)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    errors: list[ServiceError] = []
    ok, _ = mutate(
        request,
        service=COLLECTIONS,
        action="collections.update_curated",
        target_type="collection",
        target_id=str(collection_id),
        reason=reason,
        idempotency_key=idem,
        before={"version": base_version},
        audit_fields=("version", "status", "items_count"),
        call=_capturing(
            lambda c: c.update_curated(
                collection_id,
                body,
                base_version=base_version,
                reason=reason,
                idempotency_key=idem,
            ),
            errors,
        ),
        success="Опубликована новая версия сборника",
    )
    if ok:
        return redirect("collections:detail", collection_id=collection_id)
    refusal = _refusal_context(request, form, errors[0] if errors else None, collection_id)
    return _render_editor(request, form=form, **context, **refusal)
