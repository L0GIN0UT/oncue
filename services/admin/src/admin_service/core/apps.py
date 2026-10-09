"""Ядро админки: вход через identity, зеркало пользователей, клиенты сервисов."""

from __future__ import annotations

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Приложение ``core``."""

    name = "admin_service.core"
    label = "core"
    verbose_name = "Ядро"
