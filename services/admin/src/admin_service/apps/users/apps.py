"""Раздел «Пользователи» (минимум этапа 1): поиск, карточка, смена ролей через identity."""

from __future__ import annotations

from django.apps import AppConfig

from admin_service.core.menu import MenuEntry


class UsersConfig(AppConfig):
    """Приложение ``admin_users``."""

    name = "admin_service.apps.users"
    label = "admin_users"
    verbose_name = "Пользователи"
    gv_menu = (MenuEntry("Пользователи", "users:users", "users.view", "поиск, роли"),)
