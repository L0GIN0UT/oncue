"""Конфигурация приложения «Матчи»."""

from __future__ import annotations

from django.apps import AppConfig

from admin_service.core.menu import MenuEntry


class MatchesConfig(AppConfig):
    """Приложение ``admin_matches`` (этап 4a, план этапа 4 §10)."""

    name = "admin_service.apps.matches"
    label = "admin_matches"
    verbose_name = "Матчи"
    gv_menu = (
        MenuEntry(
            "Матчи",
            "matches:rooms",
            "match.manage",
            "активные комнаты, принудительное завершение, итог матча",
        ),
    )
