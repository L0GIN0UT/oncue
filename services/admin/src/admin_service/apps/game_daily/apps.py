"""Конфигурация приложения «Наборы дня»."""

from __future__ import annotations

from django.apps import AppConfig

from admin_service.core.menu import MenuEntry


class GameDailyConfig(AppConfig):
    """Приложение ``admin_game_daily``."""

    name = "admin_service.apps.game_daily"
    label = "admin_game_daily"
    verbose_name = "Наборы дня"
    gv_menu = (
        MenuEntry(
            "Наборы дня",
            "game_daily:calendar",
            "game.daily.view",
            "будущие дейлики: состав, замена единиц, перегенерация",
        ),
    )
