"""Конвертеры маршрутов: ключ режима и дата дня — проверяются до представления."""

from __future__ import annotations

from datetime import date

from django.urls import register_converter


class ModeKeyConverter:
    """Ключ режима ``music|film.<тип>.<имя>`` (как ``mode_key`` в contracts/openapi/game.json)."""

    regex = r"(?:music|film)\.[a-z0-9_]{1,40}\.[a-z0-9_]{1,40}"

    def to_python(self, value: str) -> str:
        return value

    def to_url(self, value: str) -> str:
        return value


class IsoDateConverter:
    """Дата ``YYYY-MM-DD``; несуществующая дата (2026-02-30) — 404, а не 500."""

    regex = r"\d{4}-\d{2}-\d{2}"

    def to_python(self, value: str) -> date:
        return date.fromisoformat(value)  # ValueError → Django отвечает 404

    def to_url(self, value: date | str) -> str:
        return value.isoformat() if isinstance(value, date) else value


def register() -> None:
    """Зарегистрировать конвертеры; вызывается один раз — из корневого ``urls.py``."""
    register_converter(ModeKeyConverter, "modekey")
    register_converter(IsoDateConverter, "isodate")
