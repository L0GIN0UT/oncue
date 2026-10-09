"""Представления раздела «Пользователи» — через admin-API identity."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from admin_service.apps.users.forms import RolesForm, UserSearchForm
from admin_service.core.models import AdminUser
from admin_service.core.permissions import django_perm, require_perm
from admin_service.core.views import audit_read, fetch, mutate, with_links

IDENTITY = "identity"
EMAIL_QUERY = "<e-mail>"


@require_GET
@require_perm("users.view")
def user_list(request: HttpRequest) -> HttpResponse:
    """Поиск пользователей."""
    form = UserSearchForm(request.GET or None)
    q = form.cleaned_data.get("q", "") if form.is_bound and form.is_valid() else ""
    page, error = fetch(request, IDENTITY, "/admin/v1/users", q=q)
    rows = page.get("items") if isinstance(page, dict) else page
    items = with_links(rows, "users:user", "user_id", "id", "user_id")
    if error is None:
        # Ответ содержит e-mail: кто, что искал и чьи записи увидел — в аудит, но без
        # самих персональных данных (поиск по e-mail записывается без адреса).
        audit_read(
            request,
            action="identity.view_users",
            service=IDENTITY,
            target_type="user_list",
            target_id="",
            q=EMAIL_QUERY if "@" in q else q,
            user_ids=[str(row.get("id")) for row in items],
        )
    return render(
        request,
        "admin_users/user_list.html",
        {"title": "Пользователи", "form": form, "users": items, "error": error},
    )


def _context(request: HttpRequest, user_id: UUID, **extra: Any) -> dict[str, Any]:
    user, error = fetch(request, IDENTITY, f"/admin/v1/users/{user_id}")
    if error is None:
        audit_read(
            request,
            action="identity.view_user",
            service=IDENTITY,
            target_type="user",
            target_id=str(user_id),
        )
    user = user if isinstance(user, dict) else None
    roles = ", ".join(str(r) for r in (user or {}).get("roles", []) or [])
    context: dict[str, Any] = {
        "title": (user or {}).get("display_name") or "Пользователь",
        "user_id": user_id,
        "subject": user,
        "error": error,
        "roles_form": RolesForm(initial={"roles": roles}),
        "can_assign": isinstance(request.user, AdminUser)
        and request.user.has_perm(django_perm("roles.assign")),
    }
    context.update(extra)
    return context


@require_GET
@require_perm("users.view")
def user_detail(request: HttpRequest, user_id: UUID) -> HttpResponse:
    """Карточка пользователя."""
    return render(request, "admin_users/user_detail.html", _context(request, user_id))


@require_POST
@require_perm("roles.assign")
def user_roles(request: HttpRequest, user_id: UUID) -> HttpResponse:
    """Сменить роли (полный набор). Зеркало обновится по событию ``UserRolesChanged``."""
    form = RolesForm(request.POST)
    if not form.is_valid():
        context = _context(request, user_id, roles_form=form)
        return render(request, "admin_users/user_detail.html", context, status=400)
    subject, _ = fetch(request, IDENTITY, f"/admin/v1/users/{user_id}")
    before = {"roles": subject.get("roles")} if isinstance(subject, dict) else None
    roles, reason = form.cleaned_data["roles"], form.cleaned_data["reason"]
    key = str(form.cleaned_data["idempotency_key"])
    mutate(
        request,
        service=IDENTITY,
        action="identity.assign_roles",
        target_type="user",
        target_id=str(user_id),
        reason=reason,
        idempotency_key=key,
        before=before,
        audit_fields=("roles",),
        call=lambda c: c.put(
            f"/admin/v1/users/{user_id}/roles",
            idempotency_key=key,
            json={"roles": roles, "reason": reason},
        ),
        success="Роли обновлены",
    )
    return redirect("users:user", user_id=user_id)
