"""Конфигурация приложения «События»."""

from __future__ import annotations

from django.apps import AppConfig

from admin_service.core.menu import MenuEntry


class EventsConfig(AppConfig):
    """Приложение ``admin_events`` (этап 5d, поток E2)."""

    name = "admin_service.apps.events"
    label = "admin_events"
    verbose_name = "События"
    gv_menu = (
        MenuEntry(
            "События",
            "events:list",
            "progression.events.manage",
            "сезонные события: определение, карта, запуски, публикация, статистика",
        ),
    )
