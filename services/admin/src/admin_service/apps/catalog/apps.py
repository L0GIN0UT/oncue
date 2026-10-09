"""Раздел «Каталог»: всё через admin-API catalog и media-pipeline (D3 / ADR 0013)."""

from __future__ import annotations

from django.apps import AppConfig


class CatalogConfig(AppConfig):
    """Приложение ``admin_catalog``."""

    name = "admin_service.apps.catalog"
    label = "admin_catalog"
    verbose_name = "Каталог"
