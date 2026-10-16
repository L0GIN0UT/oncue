"""Конфигурация приложения «Сборники»."""

from __future__ import annotations

from django.apps import AppConfig

from admin_service.core.menu import MenuEntry


class CollectionsConfig(AppConfig):
    """Приложение ``admin_collections`` (этап 3b, D14)."""

    name = "admin_service.apps.collections"
    label = "admin_collections"
    verbose_name = "Сборники"
    gv_menu = (
        MenuEntry(
            "Сборники",
            "collections:list",
            "collections.view",
            "пользовательские и кураторские: состав, скрытие, «рекомендуемое», авторы",
        ),
    )
