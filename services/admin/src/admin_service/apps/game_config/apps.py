"""Конфигурация приложения «Режимы»."""

from __future__ import annotations

from django.apps import AppConfig

from admin_service.core.menu import MenuEntry


class GameConfigConfig(AppConfig):
    """Приложение ``admin_game_config``."""

    name = "admin_service.apps.game_config"
    label = "admin_game_config"
    verbose_name = "Режимы игры"
    gv_menu = (
        MenuEntry(
            "Режимы",
            "game_config:modes",
            "config.modes.view",
            "конфиги режимов: черновик, проверка, публикация, откат",
        ),
    )
