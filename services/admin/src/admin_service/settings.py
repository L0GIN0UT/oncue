"""Настройки admin (переменные окружения ``GV_*``)."""

from __future__ import annotations

from gv_service import ServiceSettings


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "admin"
