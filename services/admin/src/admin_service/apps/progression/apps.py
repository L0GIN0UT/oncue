"""Конфигурация приложения «Прогрессия»."""

from __future__ import annotations

from django.apps import AppConfig

from admin_service.core.menu import MenuEntry


class ProgressionConfig(AppConfig):
    """Приложение ``admin_progression`` (этап 5a, поток P3)."""

    name = "admin_service.apps.progression"
    label = "admin_progression"
    verbose_name = "Прогрессия"
    gv_menu = (
        MenuEntry(
            "Прогрессия",
            "progression:configs",
            "progression.config.manage",
            "конфиг: версии, публикация, откат; колесо; задания и бейджи",
        ),
        MenuEntry(
            "Версы: журнал и выдача",
            "progression:ledger",
            "progression.ledger.read",
            "журнал проводок игрока, ручная выдача и сторно",
        ),
    )
