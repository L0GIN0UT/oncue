"""Circuit breaker вызовов других сервисов (R6): после серии отказов — сразу 503.

Не пускает новые запросы к упавшей зависимости на ``cooldown_s``, затем пропускает пробный.
Время — монотонные часы процесса (не ``Clock`` домена: это защита транспорта).
"""

from __future__ import annotations

import time


class CircuitBreaker:
    """``failures`` отказов подряд → разомкнут на ``cooldown_s``."""

    def __init__(self, failures: int, cooldown_s: float) -> None:
        self._threshold = failures
        self._cooldown = cooldown_s
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        """Запросы сейчас не пропускаются."""
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self._cooldown:
            # Полуоткрыт: следующий запрос — пробный, его исход решит дальше.
            self._opened_at = None
            self._failures = self._threshold - 1
            return False
        return True

    def success(self) -> None:
        """Зависимость ответила осмысленно."""
        self._failures = 0
        self._opened_at = None

    def failure(self) -> None:
        """Отказ зависимости (сеть, таймаут, 5xx)."""
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = time.monotonic()
