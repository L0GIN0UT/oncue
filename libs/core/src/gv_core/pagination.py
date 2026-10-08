"""Курсорная пагинация.

Курсор непрозрачен для клиента: это base64url от JSON с ключами сортировки последнего
элемента страницы. Offset-пагинация не используется: она медленная на больших таблицах и
даёт дубли и пропуски при вставках.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any

from gv_core.errors import InvalidInputError

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20
_MAX_CURSOR_LEN = 1024


def encode_cursor(position: dict[str, Any]) -> str:
    """Закодировать позицию (ключи сортировки последнего элемента) в курсор."""
    raw = json.dumps(position, separators=(",", ":"), sort_keys=True, default=str).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Раскодировать курсор.

    Raises:
        InvalidInputError: курсор повреждён или слишком длинный.
    """
    if len(cursor) > _MAX_CURSOR_LEN:
        raise InvalidInputError("курсор слишком длинный", code="pagination.bad_cursor")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise InvalidInputError("неверный курсор", code="pagination.bad_cursor") from exc
    if not isinstance(data, dict):
        raise InvalidInputError("неверный курсор", code="pagination.bad_cursor")
    return data


def clamp_limit(limit: int | None) -> int:
    """Ограничить размер страницы допустимым диапазоном."""
    if limit is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(limit, MAX_PAGE_SIZE))


@dataclass(frozen=True, slots=True)
class Page[T]:
    """Страница результатов: элементы и курсор следующей страницы (``None`` — конец)."""

    items: list[T]
    next_cursor: str | None
