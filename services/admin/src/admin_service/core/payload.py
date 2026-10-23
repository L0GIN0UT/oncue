"""Безопасный разбор ответов сервисов: неожиданная форма данных не роняет страницу."""

from __future__ import annotations

from typing import Any


def as_dict(value: Any) -> dict[str, Any]:
    """``value``, если это объект JSON, иначе пустой словарь."""
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[dict[str, Any]]:
    """Объекты из списка JSON (остальные элементы отбрасываются)."""
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def as_str_list(value: Any) -> list[str]:
    """Строки из списка JSON."""
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []
