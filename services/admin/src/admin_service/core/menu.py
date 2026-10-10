"""Меню разделов админки: каждый раздел объявляет свои пункты, видны только разрешённые.

Пункты объявляются в ``AppConfig.gv_menu`` своего приложения (реестр без центрального
списка): новый раздел не правит ядро. Скрытый пункт — удобство, а не защита: представления
проверяют право сами (``require_perm``), а сервис — ещё раз в admin-API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.http import HttpRequest
from django.urls import reverse


@dataclass(frozen=True, slots=True)
class MenuEntry:
    """Пункт меню раздела.

    Attributes:
        title: Название пункта.
        url_name: Имя маршрута (``namespace:name``).
        perm: Право identity, без которого пункт скрыт.
        description: Короткое пояснение на главной.
    """

    title: str
    url_name: str
    perm: str
    description: str = ""


def visible_entries(user: Any) -> list[dict[str, str]]:
    """Пункты меню, на которые у пользователя есть право (в порядке приложений)."""
    # Лениво: модуль импортируют apps.py разделов, когда модели ещё не загружены.
    from admin_service.core.models import AdminUser
    from admin_service.core.permissions import django_perm

    if not isinstance(user, AdminUser) or not user.is_active or not user.is_staff:
        return []
    entries = [
        entry for config in apps.get_app_configs() for entry in getattr(config, "gv_menu", ())
    ]
    return [
        {"title": e.title, "url": reverse(e.url_name), "description": e.description}
        for e in entries
        if user.has_perm(django_perm(e.perm))
    ]


def menu(request: HttpRequest) -> dict[str, Any]:
    """Контекст-процессор: ``gv_menu`` — видимые пункты для текущего пользователя."""
    return {"gv_menu": visible_entries(getattr(request, "user", None))}
