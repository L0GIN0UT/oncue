"""Конвертеры маршрутов: ключ режима, дата дня и код комнаты — проверяются до представления."""

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


class RoomCodeConverter:
    """Код комнаты: 8 символов ``[A-HJ-NP-Z2-9]`` (как ``code`` в contracts/openapi/match.json)."""

    regex = r"[A-HJ-NP-Z2-9]{8}"

    def to_python(self, value: str) -> str:
        return value

    def to_url(self, value: str) -> str:
        return value


class LadderKeyConverter:
    """Ключ ladder-а ``[a-z][a-z0-9_]{0,63}`` (``LadderKey`` в contracts/openapi/rating.json)."""

    regex = r"[a-z][a-z0-9_]{0,63}"

    def to_python(self, value: str) -> str:
        return value

    def to_url(self, value: str) -> str:
        return value


def register() -> None:
    """Зарегистрировать конвертеры; вызывается один раз — из корневого ``urls.py``."""
    register_converter(ModeKeyConverter, "modekey")
    register_converter(IsoDateConverter, "isodate")
    register_converter(RoomCodeConverter, "roomcode")
    register_converter(LadderKeyConverter, "ladderkey")
