"""Представления раздела «UGC-изображения» (право ``media.ugc.moderate``).

Очередь и решения - только через admin-API media-pipeline (``core.media_client``). Карточка
показывает картинку через ``preview`` - прокси admin: байты ``pending``-изображения
читаются у media-pipeline с токеном модератора и отдаются браузеру (``no-store``, ``nosniff``);
подписанных ссылок и адресов S3 в страницах нет (ADR 0061). Решение - форма с обязательной причиной
и ``Idempotency-Key``; «снять с публикации» - тот же ``reject`` из статуса ``approved``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from admin_service.apps.ugc.forms import STATUSES, ApproveForm, RejectForm
from admin_service.core.media_client import MEDIA, UGC_PREVIEW_TYPE, MediaAdminClient
from admin_service.core.payload import as_dict, as_list
from admin_service.core.permissions import require_perm
from admin_service.core.service_client import ServiceClient, ServiceError
from admin_service.core.views import (
    audit_read,
    error_status,
    mutate,
    read,
    service_error_response,
)

MODERATE = "media.ugc.moderate"
_SCAN_PAGES = 20
_PAGE = 100
_DECISION_FIELDS = ("status", "reason_code", "version")


def _client(call: Callable[[MediaAdminClient], Any]) -> Callable[[ServiceClient], Any]:
    return lambda c: call(MediaAdminClient(c))


def _status(request: HttpRequest) -> str:
    raw = request.GET.get("status", "pending")
    return raw if raw in STATUSES else "pending"


def _settings(request: HttpRequest) -> tuple[dict[str, Any], str | None]:
    data, error = read(request, MEDIA, _client(lambda c: c.ugc_settings()))
    return as_dict(data), error.message if error else None


def _reasons(settings_data: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (str(r["code"]), f"{r.get('title', r['code'])} ({r['code']})")
        for r in as_list(settings_data.get("reasons"))
        if "code" in r
    ]


def _page(request: HttpRequest, status: str, cursor: str | None) -> tuple[Any, ServiceError | None]:
    def call(client: MediaAdminClient) -> Any:
        return client.ugc_images(status=status, cursor=cursor, limit=_PAGE)

    return read(request, MEDIA, _client(call))


def _find(
    request: HttpRequest, asset_id: UUID, status: str
) -> tuple[dict[str, Any] | None, ServiceError | None]:
    """Строка очереди по id: GET по id у admin-API нет - листаем до первой страницы с ней.

    ``(None, None)`` - в этом статусе строки нет (404); ``(None, ошибка)`` - сервис не ответил.
    """
    cursor: str | None = None
    for _ in range(_SCAN_PAGES):
        data, error = _page(request, status, cursor)
        if error is not None:
            return None, error
        for row in as_list(as_dict(data).get("items")):
            if row.get("asset_id") == str(asset_id):
                return row, None
        cursor = as_dict(data).get("next_cursor")
        if not cursor:
            break
    return None, None


@require_GET
@require_perm(MODERATE)
def queue(request: HttpRequest) -> HttpResponse:
    """Очередь по статусу (по умолчанию ``pending``), фильтр по назначению, курсор."""
    status = _status(request)
    purpose = request.GET.get("purpose") or None
    cursor = request.GET.get("cursor") or None
    data, error = read(
        request,
        MEDIA,
        _client(lambda c: c.ugc_images(status=status, purpose=purpose, cursor=cursor)),
    )
    settings_data, _ = _settings(request)
    rows = as_list(as_dict(data).get("items"))
    for row in rows:
        try:
            row["card_url"] = (
                reverse("ugc:card", kwargs={"asset_id": UUID(str(row.get("asset_id")))})
                + "?"
                + urlencode({"status": status})
            )
        except ValueError:
            row["card_url"] = None
    return render(
        request,
        "admin_ugc/queue.html",
        {
            "title": "UGC-изображения",
            "error": error.message if error else None,
            "rows": rows,
            "next_cursor": as_dict(data).get("next_cursor"),
            "status": status,
            "statuses": STATUSES,
            "purpose": purpose or "",
            "purposes": [p.get("purpose") for p in as_list(settings_data.get("purposes"))],
        },
    )


def _card_page(
    request: HttpRequest,
    asset_id: UUID,
    *,
    approve_form: ApproveForm | None = None,
    reject_form: RejectForm | None = None,
    status_code: int = 200,
) -> HttpResponse:
    status = _status(request)
    row, find_error = _find(request, asset_id, status)
    if find_error is not None:
        return service_error_response(request, find_error)
    if row is None:
        raise Http404("Изображение не найдено в этом статусе (возможно, уже обработано)")
    settings_data, settings_error = _settings(request)
    suffix = "?" + urlencode({"status": status})
    context = {
        "title": "UGC-изображение",
        "row": row,
        "status": status,
        # Превью - только у pending (ADR 0061); остальным media-pipeline отвечает 409.
        "preview_url": (
            reverse("ugc:preview", kwargs={"asset_id": asset_id})
            if row.get("status") == "pending"
            else None
        ),
        "error": settings_error,
        "approve_form": approve_form or ApproveForm(auto_id="id_ap_%s"),
        "reject_form": reject_form
        or RejectForm(reasons=_reasons(settings_data), auto_id="id_rj_%s"),
        "approve_url": reverse("ugc:approve", kwargs={"asset_id": asset_id}) + suffix,
        "reject_url": reverse("ugc:reject", kwargs={"asset_id": asset_id}) + suffix,
        "reject_label": "Снять с публикации" if row.get("status") == "approved" else "Отклонить",
    }
    response = render(request, "admin_ugc/card.html", context, status=status_code)
    # Страница с данными игроков не должна оседать в кэше браузера или прокси.
    response["Cache-Control"] = "no-store, private"
    response["Pragma"] = "no-cache"
    return response


@require_GET
@require_perm(MODERATE)
def preview(request: HttpRequest, asset_id: UUID) -> HttpResponse:
    """Прокси превью ``pending``-изображения: байты из media-pipeline, токен браузеру не уходит."""
    data, error = read(request, MEDIA, _client(lambda c: c.ugc_preview(asset_id)))
    if error is not None:
        # Картинке нужен статус, а не страница: 404/409 (уже обработано) - 404, сбои - 502/503.
        status = 404 if error.status in (404, 409) else error_status(error)
        return HttpResponse(error.message, status=status, content_type="text/plain; charset=utf-8")
    audit_read(
        request,
        action="ugc.view_preview",
        service=MEDIA,
        target_type="ugc_image",
        target_id=str(asset_id),
    )
    response = HttpResponse(data, content_type=UGC_PREVIEW_TYPE)
    response["Cache-Control"] = "no-store, private"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = "inline"
    return response


@require_GET
@require_perm(MODERATE)
def card(request: HttpRequest, asset_id: UUID) -> HttpResponse:
    """Карточка с превью (картинка грузится через ``preview``) и действиями."""
    return _card_page(request, asset_id)


def _status_before(request: HttpRequest, asset_id: UUID) -> str | None:
    """Статус «до» для аудита - из реальной строки очереди, а не из ``?status=`` запроса.

    Не нашли строку (уже обработана, сервис не ответил) - ``None``: решение всё равно уходит
    в сервис, он ответит 409 ``ugc.not_pending``, и отказ попадёт в аудит.
    """
    row, _ = _find(request, asset_id, _status(request))
    status = (row or {}).get("status")
    return status if isinstance(status, str) else None


def _after(request: HttpRequest) -> HttpResponse:
    """В очередь с сообщением: после отказа (409 ``ugc.not_pending``) карточки в статусе уже нет."""
    return redirect(reverse("ugc:queue") + "?" + urlencode({"status": _status(request)}))


@require_POST
@require_perm(MODERATE)
def approve(request: HttpRequest, asset_id: UUID) -> HttpResponse:
    """Одобрить изображение."""
    form = ApproveForm(request.POST, auto_id="id_ap_%s")
    if not form.is_valid():
        return _card_page(request, asset_id, approve_form=form, status_code=400)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    mutate(
        request,
        service=MEDIA,
        action="ugc.approve",
        target_type="ugc_image",
        target_id=str(asset_id),
        reason=reason,
        idempotency_key=idem,
        before={"status": _status_before(request, asset_id)},
        audit_fields=_DECISION_FIELDS,
        call=_client(lambda c: c.ugc_approve(asset_id, reason=reason, idempotency_key=idem)),
        success="Изображение одобрено",
    )
    return _after(request)


@require_POST
@require_perm(MODERATE)
def reject(request: HttpRequest, asset_id: UUID) -> HttpResponse:
    """Отклонить (из ``pending``) или снять одобренное (из ``approved``) с кодом причины."""
    settings_data, _ = _settings(request)
    form = RejectForm(request.POST, reasons=_reasons(settings_data), auto_id="id_rj_%s")
    if not form.is_valid():
        return _card_page(request, asset_id, reject_form=form, status_code=400)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    code = form.cleaned_data["reason_code"]
    mutate(
        request,
        service=MEDIA,
        action="ugc.reject",
        target_type="ugc_image",
        target_id=str(asset_id),
        reason=reason,
        idempotency_key=idem,
        before={"status": _status_before(request, asset_id)},
        audit_fields=_DECISION_FIELDS,
        call=_client(
            lambda c: c.ugc_reject(asset_id, reason_code=code, reason=reason, idempotency_key=idem)
        ),
        success="Решение сохранено",
    )
    return _after(request)
