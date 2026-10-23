"""Конфигурация приложения «Рейтинг»."""

from __future__ import annotations

from django.apps import AppConfig

from admin_service.core.menu import MenuEntry


class RatingConfig(AppConfig):
    """Приложение ``admin_rating`` (этап 4b, план этапа 4 §10)."""

    name = "admin_service.apps.rating"
    label = "admin_rating"
    verbose_name = "Рейтинг"
    gv_menu = (
        MenuEntry(
            "Рейтинг",
            "rating:ladders",
            "rating.ladders.publish",
            "ladder-ы: версии конфига, публикация, откат; сезоны; лидерборд",
        ),
    )
