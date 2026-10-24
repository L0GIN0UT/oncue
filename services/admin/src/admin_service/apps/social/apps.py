"""Конфигурация приложения «Модерация» (social)."""

from __future__ import annotations

from django.apps import AppConfig

from admin_service.core.menu import MenuEntry


class SocialConfig(AppConfig):
    """Приложение ``admin_social`` (этап 5c, поток F4)."""

    name = "admin_service.apps.social"
    label = "admin_social"
    verbose_name = "Модерация"
    gv_menu = (
        MenuEntry(
            "Очередь модерации",
            "social:cases",
            "social.moderate",
            "жалобы игроков: взять, эскалировать, решить; игрок и его санкции",
        ),
        MenuEntry(
            "Конфиг social",
            "social:configs",
            "social.config.manage",
            "лимиты друзей и комментариев, причины жалоб, пределы санкций: версии и публикация",
        ),
    )
