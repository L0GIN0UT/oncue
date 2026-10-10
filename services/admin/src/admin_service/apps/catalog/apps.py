"""Раздел «Каталог»: всё через admin-API catalog и media-pipeline (D3 / ADR 0013)."""

from __future__ import annotations

from django.apps import AppConfig

from admin_service.core.menu import MenuEntry


class CatalogConfig(AppConfig):
    """Приложение ``admin_catalog``."""

    name = "admin_service.apps.catalog"
    label = "admin_catalog"
    verbose_name = "Каталог"
    gv_menu = (
        # Каталог виден всем с доступом в админку (core.permissions.STAFF_PERM; модуль прав
        # тянет модели, а apps.py импортируется до готовности реестра приложений).
        MenuEntry("Каталог", "catalog:items", "admin.access", "поиск, блокировка, алиасы, ассеты"),
        MenuEntry(
            "Импорт",
            "catalog:import_new",
            "catalog.import",
            "TMDB, файл, MusicBrainz, Spotify, Яндекс Музыка",
        ),
    )
