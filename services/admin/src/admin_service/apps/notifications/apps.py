"""Конфигурация приложения «Рассылки»."""

from __future__ import annotations

from django.apps import AppConfig

from admin_service.core.menu import MenuEntry


class NotificationsConfig(AppConfig):
    """Приложение ``admin_notifications`` (этап 5b, поток S4)."""

    name = "admin_service.apps.notifications"
    label = "admin_notifications"
    verbose_name = "Рассылки"
    gv_menu = (
        MenuEntry(
            "Рассылки",
            "broadcasts:list",
            "notifications.broadcast.send",
            "объявления всем игрокам: создание с подтверждением и история",
        ),
    )
