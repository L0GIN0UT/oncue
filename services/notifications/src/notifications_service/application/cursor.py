"""Непрозрачный курсор ``(время, id)``: base64url от JSON (формат ``[A-Za-z0-9_-]``)."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from uuid import UUID

from gv_core.errors import InvalidInputError


def encode_cursor(at: datetime, row_id: UUID) -> str:
    """Курсор позиции последней записи страницы."""
    raw = json.dumps({"at": at.isoformat(), "id": str(row_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Разобрать курсор.

    Raises:
        InvalidInputError: ``cursor.invalid`` - курсор повреждён.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        at = datetime.fromisoformat(data["at"])
        row_id = UUID(data["id"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise InvalidInputError("неверный курсор", code="cursor.invalid") from exc
    if at.tzinfo is None:  # время без пояса сравнивалось бы с timestamptz неверно
        raise InvalidInputError("неверный курсор", code="cursor.invalid")
    return at, row_id
