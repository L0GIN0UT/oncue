"""Генерация идентификаторов UUID v7 (RFC 9562).

UUID v7 упорядочен по времени создания: хорошо ложится в B-tree индексы и не раскрывает
количество записей, в отличие от автоинкремента. В Python 3.13 стандартного ``uuid7`` нет,
поэтому реализуем сами.

Монотонность внутри одного процесса обеспечивается методом 1 из RFC 9562 §6.2:
12 бит ``rand_a`` используются как счётчик для идентификаторов, созданных в одну миллисекунду.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid

_MAX_COUNTER = 0xFFF  # 12 бит
_lock = threading.Lock()
_last_ms = -1
_counter = 0


def uuid7(timestamp_ms: int | None = None) -> uuid.UUID:
    """Вернуть новый UUID v7.

    Args:
        timestamp_ms: Unix-время в миллисекундах. Передаётся только в тестах; по умолчанию
            берётся текущее время.

    Returns:
        UUID версии 7. Идентификаторы, созданные одним процессом, строго возрастают.
    """
    global _last_ms, _counter  # noqa: PLW0603 — состояние генератора намеренно модульное

    with _lock:
        ms = time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms
        if ms <= _last_ms:
            # Та же (или «откатившаяся назад») миллисекунда: увеличиваем счётчик.
            ms = _last_ms
            _counter += 1
            if _counter > _MAX_COUNTER:
                # Счётчик переполнен — занимаем следующую миллисекунду.
                ms += 1
                _counter = secrets.randbits(11)  # старший бит 0 — оставляем запас на рост
        else:
            _counter = secrets.randbits(11)
        _last_ms = ms

        value = (ms & 0xFFFF_FFFF_FFFF) << 80
        value |= 0x7 << 76  # версия
        value |= _counter << 64
        value |= 0b10 << 62  # вариант RFC 9562
        value |= secrets.randbits(62)
    return uuid.UUID(int=value)


def uuid7_timestamp_ms(value: uuid.UUID) -> int:
    """Извлечь Unix-время в миллисекундах из UUID v7."""
    if value.version != 7:  # noqa: PLR2004
        raise ValueError(f"ожидался UUID v7, получен v{value.version}")
    return value.int >> 80
