"""Конфигурация приложения «UGC-изображения»."""

from __future__ import annotations

from django.apps import AppConfig

from admin_service.core.menu import MenuEntry


class UgcConfig(AppConfig):
    """Приложение ``admin_ugc`` (этап 5b, поток S4)."""

    name = "admin_service.apps.ugc"
    label = "admin_ugc"
    verbose_name = "UGC-изображения"
    gv_menu = (
        MenuEntry(
            "UGC-изображения",
            "ugc:queue",
            "media.ugc.moderate",
            "очередь модерации картинок игроков: одобрить, отклонить, снять с публикации",
        ),
    )
