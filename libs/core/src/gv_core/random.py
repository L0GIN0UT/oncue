"""Порт «случайность».

Домен и use-cases не вызывают модуль ``random`` напрямую: случайность приходит через
``Random`` (docs/03-code-standards.md §3). Это даёт воспроизводимые игры по seed и
детерминированные тесты (``gv_testkit.fakes.FakeRandom``).

Реализации:
    * ``SystemRandom`` — криптостойкий источник ОС (seed игровых сессий, токены);
    * ``SeededRandom`` — детерминированный генератор от байтового seed (наборы дня,
      розыгрыш раундов сессии): одинаковый seed — одинаковая последовательность.
"""

from __future__ import annotations

import hashlib
import random as _stdlib_random
from collections.abc import MutableSequence, Sequence
from typing import Protocol


class Random(Protocol):
    """Источник случайности."""

    def randrange(self, start: int, stop: int | None = None, step: int = 1) -> int:
        """Случайное целое из ``range(start, stop, step)``."""
        ...

    def sample[T](self, population: Sequence[T], k: int) -> list[T]:
        """``k`` различных элементов ``population`` в случайном порядке."""
        ...

    def shuffle[T](self, items: MutableSequence[T]) -> None:
        """Перемешать последовательность на месте."""
        ...

    def random(self) -> float:
        """Случайное число из ``[0.0, 1.0)``."""
        ...


class SystemRandom:
    """Криптостойкая случайность ОС (``random.SystemRandom``); seed не воспроизводится."""

    def __init__(self) -> None:
        self._rng = _stdlib_random.SystemRandom()

    def randrange(self, start: int, stop: int | None = None, step: int = 1) -> int:
        """Случайное целое из ``range(start, stop, step)``."""
        return self._rng.randrange(start, stop, step)

    def sample[T](self, population: Sequence[T], k: int) -> list[T]:
        """``k`` различных элементов в случайном порядке."""
        return self._rng.sample(population, k)

    def shuffle[T](self, items: MutableSequence[T]) -> None:
        """Перемешать на месте."""
        self._rng.shuffle(items)

    def random(self) -> float:
        """Число из ``[0.0, 1.0)``."""
        return self._rng.random()


class SeededRandom:
    """Детерминированный генератор от байтового seed.

    Seed хэшируется sha512 и передаётся в Mersenne Twister стандартной библиотеки: так
    последовательность зависит от всех байтов seed и не меняется между версиями Python
    (``random.seed(int)`` стабилен). Для непредсказуемости seed должен быть секретным
    (например, ``hmac(secret, mode_key|day)`` у набора дня).

    Args:
        seed: Байты seed (не пустые).
    """

    def __init__(self, seed: bytes) -> None:
        if not seed:
            raise ValueError("seed не может быть пустым")
        digest = hashlib.sha512(seed).digest()
        # S311: детерминизм здесь и нужен; криптостойкость обеспечивает секретный seed.
        self._rng = _stdlib_random.Random(int.from_bytes(digest, "big"))  # noqa: S311

    def randrange(self, start: int, stop: int | None = None, step: int = 1) -> int:
        """Случайное целое из ``range(start, stop, step)``."""
        return self._rng.randrange(start, stop, step)

    def sample[T](self, population: Sequence[T], k: int) -> list[T]:
        """``k`` различных элементов в случайном порядке."""
        return self._rng.sample(population, k)

    def shuffle[T](self, items: MutableSequence[T]) -> None:
        """Перемешать на месте."""
        self._rng.shuffle(items)

    def random(self) -> float:
        """Число из ``[0.0, 1.0)``."""
        return self._rng.random()
