"""Общее для роутеров game."""

from __future__ import annotations

from typing import NoReturn

from gv_core.errors import NotImplementedYetError


def not_implemented() -> NoReturn:
    """Заглушка шага 0a: контракт ручки зафиксирован, реализация — поток B (501)."""
    raise NotImplementedYetError("ручка ещё не реализована (план этапа 2, поток B)")
