"""Политика повторов для обработчиков событий и задач."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Экспоненциальная задержка с полным jitter.

    Attributes:
        max_attempts: Сколько всего попыток (включая первую) до отправки в DLQ.
        base_delay: Задержка перед второй попыткой, секунды.
        factor: Множитель роста задержки.
        max_delay: Верхняя граница задержки, секунды.
    """

    max_attempts: int = 3
    base_delay: float = 0.5
    factor: float = 4.0
    max_delay: float = 10.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts должен быть >= 1")
        if self.base_delay < 0 or self.max_delay < 0 or self.factor < 1:
            raise ValueError("недопустимые параметры задержки")

    def delay(self, attempt: int, rng: random.Random | None = None) -> float:
        """Задержка после неудачной попытки номер ``attempt`` (с 1).

        Полный jitter (случайное значение от 0 до потолка) разводит повторы разных
        экземпляров во времени и не даёт им бить в зависимость синхронно.
        """
        ceiling = min(self.max_delay, self.base_delay * self.factor ** (attempt - 1))
        return (rng or random).uniform(0, ceiling)

    def should_retry(self, attempt: int) -> bool:
        """Есть ли ещё попытки после неудачной попытки номер ``attempt``."""
        return attempt < self.max_attempts
