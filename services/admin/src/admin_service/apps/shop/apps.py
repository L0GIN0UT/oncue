"""Конфигурация приложения «Магазин»."""

from __future__ import annotations

from django.apps import AppConfig

from admin_service.core.menu import MenuEntry


class ShopConfig(AppConfig):
    """Приложение ``admin_shop`` (этап 5b, поток S4)."""

    name = "admin_service.apps.shop"
    label = "admin_shop"
    verbose_name = "Магазин"
    gv_menu = (
        MenuEntry(
            "Магазин",
            "shop:items",
            "progression.items.manage",
            "предметы, картинки и анимации, паки",
        ),
        MenuEntry(
            "Промокоды",
            "shop:promos",
            "progression.promo.manage",
            "партии промокодов: создание (коды показываются один раз) и отзыв",
        ),
    )
