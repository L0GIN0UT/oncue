"""Представления раздела «Рассылки» (право ``notifications.broadcast.send``).

Отправка - в два шага: форма проверяется и показывается страница подтверждения (сервис не
вызывается), и только ``POST send/`` ставит рассылку в очередь с ``Idempotency-Key`` формы.
Число получателей API до отправки не отдаёт: оно появляется в списке после постановки.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from admin_service.apps.notifications.forms import BroadcastForm
from admin_service.core.notifications_client import NOTIFICATIONS, NotificationsAdminClient
from admin_service.core.payload import as_dict, as_list
from admin_service.core.permissions import require_perm
from admin_service.core.service_client import ServiceClient
from admin_service.core.views import mutate, read

SEND = "notifications.broadcast.send"


def _client(call: Callable[[NotificationsAdminClient], Any]) -> Callable[[ServiceClient], Any]:
    return lambda c: call(NotificationsAdminClient(c))


@require_GET
@require_perm(SEND)
def broadcast_list(request: HttpRequest) -> HttpResponse:
    """Рассылки: тема, статус, число получателей (курсор)."""
    cursor = request.GET.get("cursor") or None
    data, error = read(request, NOTIFICATIONS, _client(lambda c: c.broadcasts(cursor=cursor)))
    return render(
        request,
        "admin_notifications/list.html",
        {
            "title": "Рассылки",
            "error": error.message if error else None,
            "rows": as_list(as_dict(data).get("items")),
            "next_cursor": as_dict(data).get("next_cursor"),
        },
    )


@require_http_methods(["GET", "POST"])
@require_perm(SEND)
def broadcast_new(request: HttpRequest) -> HttpResponse:
    """Шаг 1: форма; после проверки - страница подтверждения без вызова сервиса."""
    if request.method == "POST":
        form = BroadcastForm(request.POST)
        if form.is_valid():
            return render(
                request,
                "admin_notifications/confirm.html",
                {"title": "Подтвердите рассылку", "form": form},
            )
        return render(
            request,
            "admin_notifications/new.html",
            {"title": "Новая рассылка", "form": form},
            status=400,
        )
    return render(
        request,
        "admin_notifications/new.html",
        {"title": "Новая рассылка", "form": BroadcastForm()},
    )


@require_POST
@require_perm(SEND)
def broadcast_send(request: HttpRequest) -> HttpResponse:
    """Шаг 2: подтверждённая отправка (ключ идемпотентности - из формы шага 1)."""
    form = BroadcastForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "admin_notifications/new.html",
            {"title": "Новая рассылка", "form": form},
            status=400,
        )
    data, idem = form.cleaned_data, str(form.cleaned_data["idempotency_key"])
    ok, _ = mutate(
        request,
        service=NOTIFICATIONS,
        action="notifications.broadcast",
        target_type="broadcast",
        target_id=idem,
        reason=data["reason"],
        idempotency_key=idem,
        before={"subject": data["subject"], "body_length": len(data["body"])},
        audit_fields=("id", "status", "recipients"),
        call=_client(
            lambda c: c.create_broadcast(
                subject=data["subject"],
                body=data["body"],
                reason=data["reason"],
                idempotency_key=idem,
            )
        ),
        success="Рассылка поставлена в очередь",
    )
    if ok:
        return redirect("broadcasts:list")
    return render(
        request,
        "admin_notifications/confirm.html",
        {"title": "Подтвердите рассылку", "form": form},
        status=400,
    )
