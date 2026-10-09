"""Синхронизация прав identity → Django и проверка прав в представлениях."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import wraps
from typing import Any

from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse

from admin_service.core.models import AdminUser
from admin_service.core.service_client import SessionExpiredError

STAFF_PERM = "admin.access"
SUPERUSER_ROLE = "admin"
PERM_PREFIX = "identity."
GROUP_PREFIX = "identity:"


def django_perm(perm: str) -> str:
    """Имя права identity в пространстве Django (``identity.catalog.block_item``)."""
    return f"{PERM_PREFIX}{perm}"


class PermissionSync:
    """Переносит роли и права identity на зеркального пользователя.

    - ``is_staff`` — есть право ``admin.access`` (без него в админку не пускаем);
    - ``is_superuser`` — роль ``admin`` (у неё все права, docs/plans/stage-1.md §2.6);
    - группы Django ``identity:<роль>`` — для фильтров и наглядности в списках;
    - сами права — снимок ``identity_perms``, по нему работает ``user.has_perm``.
    """

    @staticmethod
    def apply(user: AdminUser, *, roles: Iterable[str], perms: Iterable[str]) -> None:
        """Применить полный снимок ролей и прав и сохранить пользователя."""
        role_set, perm_set = set(roles), set(perms)
        user.identity_roles = sorted(role_set)
        user.identity_perms = sorted(perm_set)
        user.is_staff = STAFF_PERM in perm_set
        user.is_superuser = user.is_staff and SUPERUSER_ROLE in role_set
        user.save()
        groups = [
            Group.objects.get_or_create(name=f"{GROUP_PREFIX}{r}")[0] for r in sorted(role_set)
        ]
        user.groups.set(groups)

    @staticmethod
    def revoke(user: AdminUser) -> None:
        """Отозвать доступ (аккаунт удалён в identity)."""
        user.identity_roles = []
        user.identity_perms = []
        user.is_staff = False
        user.is_superuser = False
        user.is_active = False
        user.save()
        user.groups.clear()


ViewFunc = Callable[..., HttpResponse]


def require_perm(perm: str) -> Callable[[ViewFunc], ViewFunc]:
    """Декоратор представления: нужен вход в админку и право identity ``perm``.

    Анонимный — на страницу входа; без права — 403. Сервис проверяет право ещё раз сам
    (``require_perm`` в admin-API), здесь проверка нужна, чтобы не показывать формы зря.
    """

    def decorator(view: ViewFunc) -> ViewFunc:
        @wraps(view)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            from django.contrib.auth.views import redirect_to_login

            user = request.user
            if not isinstance(user, AdminUser) or not user.is_active or not user.is_staff:
                return redirect_to_login(request.get_full_path())
            if not user.has_perm(django_perm(perm)):
                raise PermissionDenied
            try:
                return view(request, *args, **kwargs)
            except SessionExpiredError:
                from django.contrib import messages
                from django.contrib.auth import logout

                logout(request)
                messages.warning(request, "Сессия истекла — войдите заново")
                return redirect_to_login(request.get_full_path())

        return wrapper

    return decorator
