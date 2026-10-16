"""Представления раздела «Каталог».

Чтение и запись — через admin-API catalog и media-pipeline (ADR 0013); права проверяются
здесь (чтобы не показывать формы зря) и ещё раз в сервисе. Каждое действие пишет аудит.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from admin_service.apps.catalog.forms import (
    AliasesForm,
    ApproveClueTextsForm,
    ClueTextsForm,
    ImportForm,
    ReasonForm,
    RepublishForm,
    SearchForm,
    SourcePositionForm,
    SourceUploadForm,
)
from admin_service.core.catalog_client import CATALOG, CatalogAdminClient
from admin_service.core.media_client import MEDIA, MediaAdminClient
from admin_service.core.permissions import STAFF_PERM, has_perm, require_perm
from admin_service.core.views import fetch, mutate, read, with_links

EDIT_TEXTS = "catalog.edit_aliases"  # тексты загадок — то же право, что алиасы (§2.7 плана 3a)
UPLOAD = "media.upload_source"
IMPORT = "catalog.import"


def _aliases_text(item: dict[str, Any] | None) -> str:
    aliases = (item or {}).get("aliases", []) or []
    lines = [
        f"{a.get('text', '')} | {a.get('lang') or ''} | {a.get('kind', '')}"
        for a in aliases
        if isinstance(a, dict)
    ]
    return "\n".join(lines)


@require_GET
@require_perm(STAFF_PERM)
def item_list(request: HttpRequest) -> HttpResponse:
    """Список и поиск единиц каталога."""
    form = SearchForm(request.GET or None)
    params: dict[str, Any] = {}
    if form.is_bound and form.is_valid():
        params = {k: v for k, v in form.cleaned_data.items() if v}
    page, error = fetch(request, CATALOG, "/admin/v1/items", **params)
    page = page if isinstance(page, dict) else {}
    return render(
        request,
        "admin_catalog/item_list.html",
        {
            "title": "Каталог",
            "form": form,
            "items": with_links(
                page.get("items"), "catalog:item", "item_id", "id", "media_item_id"
            ),
            "next_cursor": page.get("next_cursor"),
            "params": {k: v for k, v in params.items() if k != "cursor"},
            "error": error,
        },
    )


def _asset_sets(item_id: UUID, rows: Any) -> list[dict[str, Any]]:
    result = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            set_id = UUID(str(row.get("asset_set_id")))
        except ValueError:
            url = None
        else:
            url = reverse("catalog:regenerate", kwargs={"item_id": item_id, "asset_set_id": set_id})
        result.append({**row, "regenerate_url": url})
    return result


def _item_context(request: HttpRequest, item_id: UUID, **forms: Any) -> dict[str, Any]:
    item, error = fetch(request, CATALOG, f"/admin/v1/items/{item_id}")
    asset_sets, assets_error = fetch(
        request, MEDIA, "/admin/v1/asset-sets", media_item_id=str(item_id)
    )
    item = item if isinstance(item, dict) else None
    context: dict[str, Any] = {
        "title": (item or {}).get("title") or "Единица каталога",
        "item_id": item_id,
        "item": item,
        "error": error,
        "asset_sets": _asset_sets(item_id, asset_sets),
        "assets_error": assets_error,
        "status_form": ReasonForm(),
        "aliases_form": AliasesForm(initial={"aliases": _aliases_text(item)}),
        "upload_form": SourceUploadForm(),
        "position_form": SourcePositionForm(),
        "regenerate_form": ReasonForm(),
        "can_upload": has_perm(request, UPLOAD),
    }
    context.update(forms)
    return context


@require_GET
@require_perm(STAFF_PERM)
def item_detail(request: HttpRequest, item_id: UUID) -> HttpResponse:
    """Карточка единицы: данные, алиасы, ассеты и формы действий."""
    return render(request, "admin_catalog/item_detail.html", _item_context(request, item_id))


def _status_change(request: HttpRequest, item_id: UUID, *, block: bool) -> HttpResponse:
    form = ReasonForm(request.POST)
    if not form.is_valid():
        context = _item_context(request, item_id, status_form=form)
        return render(request, "admin_catalog/item_detail.html", context, status=400)
    item, _ = fetch(request, CATALOG, f"/admin/v1/items/{item_id}")
    before = {"status": item.get("status")} if isinstance(item, dict) else None
    verb = "block" if block else "unblock"
    reason = form.cleaned_data["reason"]
    key = str(form.cleaned_data["idempotency_key"])
    mutate(
        request,
        service=CATALOG,
        action=f"catalog.{verb}_item",
        target_type="media_item",
        target_id=str(item_id),
        reason=reason,
        idempotency_key=key,
        before=before,
        audit_fields=("status",),
        call=lambda c: c.post(
            f"/admin/v1/items/{item_id}/{verb}", idempotency_key=key, json={"reason": reason}
        ),
        success="Единица заблокирована" if block else "Единица разблокирована",
    )
    return redirect("catalog:item", item_id=item_id)


@require_POST
@require_perm("catalog.block_item")
def item_block(request: HttpRequest, item_id: UUID) -> HttpResponse:
    """Заблокировать единицу (пропадает из выдачи, ссылки на ассеты — 410)."""
    return _status_change(request, item_id, block=True)


@require_POST
@require_perm("catalog.block_item")
def item_unblock(request: HttpRequest, item_id: UUID) -> HttpResponse:
    """Разблокировать единицу."""
    return _status_change(request, item_id, block=False)


@require_POST
@require_perm("catalog.edit_aliases")
def item_aliases(request: HttpRequest, item_id: UUID) -> HttpResponse:
    """Полностью заменить алиасы единицы."""
    form = AliasesForm(request.POST)
    if not form.is_valid():
        context = _item_context(request, item_id, aliases_form=form)
        return render(request, "admin_catalog/item_detail.html", context, status=400)
    item, _ = fetch(request, CATALOG, f"/admin/v1/items/{item_id}")
    before = {"aliases": item.get("aliases")} if isinstance(item, dict) else None
    reason, aliases = form.cleaned_data["reason"], form.cleaned_data["aliases"]
    key = str(form.cleaned_data["idempotency_key"])
    mutate(
        request,
        service=CATALOG,
        action="catalog.edit_aliases",
        target_type="media_item",
        target_id=str(item_id),
        reason=reason,
        idempotency_key=key,
        before=before,
        audit_fields=("aliases",),
        call=lambda c: c.put(
            f"/admin/v1/items/{item_id}/aliases",
            idempotency_key=key,
            json={"aliases": aliases, "reason": reason},
        ),
        success="Алиасы сохранены",
    )
    return redirect("catalog:item", item_id=item_id)


@require_POST
@require_perm(UPLOAD)
def source_upload(request: HttpRequest, item_id: UUID) -> HttpResponse:
    """Загрузить исходник в media-pipeline (multipart: единица, вид, порядок, происхождение)."""
    form = SourceUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        context = _item_context(request, item_id, upload_form=form)
        return render(request, "admin_catalog/item_detail.html", context, status=400)
    upload = form.cleaned_data["file"]
    fields = form.fields_payload(str(item_id))
    key = str(form.cleaned_data["idempotency_key"])
    ok, result = mutate(
        request,
        service=MEDIA,
        action="media.upload_source",
        target_type="media_item",
        target_id=str(item_id),
        reason=fields["reason"],
        idempotency_key=key,
        audit_after={
            "kind": fields["kind"],
            "position": form.cleaned_data["position"],
            "origin": fields.get("origin"),
            "source_url": fields.get("source_url"),
            "file_name": upload.name,
            "bytes": upload.size,
        },
        call=lambda c: MediaAdminClient(c).upload_source(
            fields=fields,
            file_name=upload.name or "upload",
            file=upload.file,
            content_type=upload.content_type or "application/octet-stream",
            idempotency_key=key,
        ),
        success="Исходник загружен, ассеты будут перегенерированы",
    )
    if ok and isinstance(result, dict) and result.get("source_media_id"):
        # id исходника нужен для смены порядка кадров: списка исходников в admin-API нет.
        messages.info(
            request,
            f"Исходник {result['source_media_id']}: {result.get('kind')}, "
            f"порядок {result.get('position')}",
        )
    return redirect("catalog:item", item_id=item_id)


@require_POST
@require_perm(UPLOAD)
def source_position(request: HttpRequest, item_id: UUID) -> HttpResponse:
    """Сменить порядок исходника (кадры последовательности): наборы перегенерируются."""
    form = SourcePositionForm(request.POST)
    if not form.is_valid():
        context = _item_context(request, item_id, position_form=form)
        return render(request, "admin_catalog/item_detail.html", context, status=400)
    source_id: UUID = form.cleaned_data["source_media_id"]
    position: int = form.cleaned_data["position"]
    reason, key = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    mutate(
        request,
        service=MEDIA,
        action="media.set_source_position",
        target_type="source_media",
        target_id=str(source_id),
        reason=reason,
        idempotency_key=key,
        before={"media_item_id": str(item_id)},
        audit_fields=("position", "kind", "media_item_id"),
        call=lambda c: MediaAdminClient(c).set_source_position(
            source_id, position=position, reason=reason, idempotency_key=key
        ),
        success=f"Порядок исходника изменён на {position}, ассеты будут перегенерированы",
    )
    return redirect("catalog:item", item_id=item_id)


# ---------------------------------------------------------------------------- тексты загадок


def _texts_context(request: HttpRequest, item_id: UUID, **forms: Any) -> dict[str, Any]:
    """Тексты загадок единицы и формы правки и одобрения (только с правом правки)."""
    item, _ = fetch(request, CATALOG, f"/admin/v1/items/{item_id}")
    texts, error = read(request, CATALOG, lambda c: CatalogAdminClient(c).clue_texts(item_id))
    texts = texts if isinstance(texts, dict) else None
    revision = (texts or {}).get("revision")
    revision = revision if isinstance(revision, int) and not isinstance(revision, bool) else None
    can_edit = has_perm(request, EDIT_TEXTS)
    context: dict[str, Any] = {
        "title": f"Тексты загадок: {(item or {}).get('title') or item_id}",
        "item_id": item_id,
        "item": item if isinstance(item, dict) else None,
        "texts": texts,
        "error": error.message if error is not None else None,
        "can_edit": can_edit and texts is not None and revision is not None,
        # Одобрять есть что, только если черновик непустой и ревизия >= 1 (ApproveClueTexts).
        "can_approve": can_edit
        and texts is not None
        and texts.get("status") == "draft"
        and (revision or 0) >= 1,
        "form": ClueTextsForm(
            initial={
                "plot_masked": (texts or {}).get("plot_masked") or "",
                "tagline": (texts or {}).get("tagline") or "",
                "emoji": (texts or {}).get("emoji") or "",
                "base_revision": revision,
            }
        ),
        "approve_form": ApproveClueTextsForm(initial={"revision": revision}),
    }
    context.update(forms)
    if not context["can_edit"]:  # без права правки — только просмотр
        for field in context["form"].fields.values():
            field.disabled = True
    return context


def _render_texts(
    request: HttpRequest, item_id: UUID, *, status: int = 200, **forms: Any
) -> HttpResponse:
    context = _texts_context(request, item_id, **forms)
    return render(request, "admin_catalog/clue_texts.html", context, status=status)


@require_GET
@require_perm(STAFF_PERM)
def clue_texts(request: HttpRequest, item_id: UUID) -> HttpResponse:
    """Редактор текстов загадок: исходный сюжет рядом с маскированным, слоган, эмодзи."""
    return _render_texts(request, item_id)


@require_POST
@require_perm(EDIT_TEXTS)
def clue_texts_save(request: HttpRequest, item_id: UUID) -> HttpResponse:
    """Сохранить тексты (снова ``draft``); при отказе правка остаётся в форме.

    В аудит — ревизия и статус, не сами тексты: ревизия однозначно указывает на них в catalog.
    """
    form = ClueTextsForm(request.POST)
    if not form.is_valid():
        return _render_texts(request, item_id, status=400, form=form)
    texts, base = form.texts(), form.cleaned_data["base_revision"]
    reason, key = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    ok, _ = mutate(
        request,
        service=CATALOG,
        action="catalog.edit_clue_texts",
        target_type="media_item",
        target_id=str(item_id),
        reason=reason,
        idempotency_key=key,
        before={"revision": base},
        audit_fields=("revision", "status"),
        call=lambda c: CatalogAdminClient(c).save_clue_texts(
            item_id, base_revision=base, reason=reason, idempotency_key=key, **texts
        ),
        success="Тексты сохранены — нужно одобрение, чтобы они попали в игру",
    )
    if not ok:
        return _render_texts(request, item_id, form=form)
    return redirect("catalog:clue_texts", item_id=item_id)


@require_POST
@require_perm(EDIT_TEXTS)
def clue_texts_approve(request: HttpRequest, item_id: UUID) -> HttpResponse:
    """Одобрить тексты ревизии ``revision``: уходят в ``MediaItemUpdated`` (version+1)."""
    form = ApproveClueTextsForm(request.POST)
    if not form.is_valid():
        return _render_texts(request, item_id, status=400, approve_form=form)
    revision: int = form.cleaned_data["revision"]
    reason, key = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    mutate(
        request,
        service=CATALOG,
        action="catalog.approve_clue_texts",
        target_type="media_item",
        target_id=str(item_id),
        reason=reason,
        idempotency_key=key,
        before={"revision": revision},
        audit_fields=("revision", "status", "approved_revision"),
        call=lambda c: CatalogAdminClient(c).approve_clue_texts(
            item_id, revision=revision, reason=reason, idempotency_key=key
        ),
        success=f"Тексты ревизии r{revision} одобрены",
    )
    return redirect("catalog:clue_texts", item_id=item_id)


# ---------------------------------------------------------------------------- переопубликация


@require_http_methods(["GET", "POST"])
@require_perm(IMPORT)
def republish(request: HttpRequest) -> HttpResponse:
    """«Переопубликовать единицы»: catalog заново шлёт ``MediaItemUpdated`` порциями (D9).

    Нужен один раз после выкладки этапа 3a (новые поля у потребителей), поэтому — явное
    подтверждение и причина.
    """
    form = RepublishForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        domain = form.cleaned_data["domain"] or None
        reason, key = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
        ok, result = mutate(
            request,
            service=CATALOG,
            action="catalog.republish",
            target_type="catalog",
            target_id=domain or "all",
            reason=reason,
            idempotency_key=key,
            audit_fields=("job_id",),
            call=lambda c: CatalogAdminClient(c).republish(
                domain=domain, reason=reason, idempotency_key=key
            ),
            success="Переопубликация поставлена в очередь",
        )
        if ok:
            job = result.get("job_id") if isinstance(result, dict) else None
            if job:
                messages.info(request, f"Задание переопубликации: {job}")
            return redirect("catalog:republish")
    status = 400 if request.method == "POST" and form.errors else 200
    return render(
        request,
        "admin_catalog/republish.html",
        {"title": "Переопубликовать единицы", "form": form},
        status=status,
    )


@require_POST
@require_perm("media.regenerate")
def asset_set_regenerate(request: HttpRequest, item_id: UUID, asset_set_id: UUID) -> HttpResponse:
    """Перегенерировать набор ассетов."""
    form = ReasonForm(request.POST)
    if not form.is_valid():
        context = _item_context(request, item_id, regenerate_form=form)
        return render(request, "admin_catalog/item_detail.html", context, status=400)
    reason = form.cleaned_data["reason"]
    key = str(form.cleaned_data["idempotency_key"])
    mutate(
        request,
        service=MEDIA,
        action="media.regenerate",
        target_type="asset_set",
        target_id=str(asset_set_id),
        reason=reason,
        idempotency_key=key,
        audit_fields=("status", "generation"),
        call=lambda c: c.post(
            f"/admin/v1/asset-sets/{asset_set_id}/regenerate",
            idempotency_key=key,
            json={"reason": reason},
        ),
        success="Перегенерация запущена",
    )
    return redirect("catalog:item", item_id=item_id)


@require_http_methods(["GET", "POST"])
@require_perm("catalog.import")
def import_new(request: HttpRequest) -> HttpResponse:
    """Запуск импорта (TMDB, файл, MusicBrainz, Spotify, Яндекс Музыка)."""
    form = ImportForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        payload, reason = form.payload(), form.cleaned_data["reason"]
        key = str(form.cleaned_data["idempotency_key"])
        ok, result = mutate(
            request,
            service=CATALOG,
            action="catalog.import",
            target_type="import",
            target_id=payload.get("ref") or payload.get("file_id", ""),
            reason=reason,
            idempotency_key=key,
            audit_fields=("import_id", "status"),
            call=lambda c: c.post(
                "/admin/v1/imports", idempotency_key=key, json={**payload, "reason": reason}
            ),
            success="Импорт поставлен в очередь",
        )
        import_id = result.get("import_id") if ok and isinstance(result, dict) else None
        if import_id:
            try:
                return redirect("catalog:import", import_id=UUID(str(import_id)))
            except ValueError:
                pass
        if ok:
            return redirect("catalog:items")
    status = 400 if request.method == "POST" and form.errors else 200
    return render(
        request, "admin_catalog/import_new.html", {"title": "Импорт", "form": form}, status=status
    )


@require_GET
@require_perm("catalog.import")
def import_status(request: HttpRequest, import_id: UUID) -> HttpResponse:
    """Статус импорта."""
    job, error = fetch(request, CATALOG, f"/admin/v1/imports/{import_id}")
    return render(
        request,
        "admin_catalog/import_status.html",
        {"title": "Статус импорта", "import_id": import_id, "job": job, "error": error},
    )
