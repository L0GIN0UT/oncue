"""Раздел «Пользователи» (минимум этапа 1): поиск, карточка, смена ролей через identity."""

from __future__ import annotations

from django.apps import AppConfig


class UsersConfig(AppConfig):
    """Приложение ``admin_users``."""

    name = "admin_service.apps.users"
    label = "admin_users"
    verbose_name = "Пользователи"
