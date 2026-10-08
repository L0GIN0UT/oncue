"""Порт «часы».

Домен и use-cases никогда не вызывают ``datetime.now()`` напрямую: время приходит через
``Clock``. Это делает логику детерминированной в тестах (см. ``gv_testkit.fakes.FakeClock``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Источник текущего времени."""

    def now(self) -> datetime:
        """Текущий момент в UTC (timezone-aware)."""
        ...


class SystemClock:
    """Реальные системные часы."""

    def now(self) -> datetime:
        """Текущий момент в UTC."""
        return datetime.now(UTC)
