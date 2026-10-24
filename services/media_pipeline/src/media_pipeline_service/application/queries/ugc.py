"""Очередь модерации UGC-изображений (``GET /admin/v1/ugc-images``)."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from uuid import UUID

from gv_core.errors import InvalidInputError
from media_pipeline_service.application.ports import UnitOfWorkFactory
from media_pipeline_service.application.ugc import UgcImageView, UgcPage
from media_pipeline_service.domain.ugc import UgcStatus


def encode_cursor(submitted_at: datetime, image_id: UUID) -> str:
    """Курсор очереди: ``(submitted_at, id)``, base64url без заполнителя."""
    raw = f"{submitted_at.isoformat()}|{image_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Разобрать курсор.

    Raises:
        InvalidInputError: ``ugc.cursor_invalid`` - курсор не наш или повреждён.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        stamp, _, ident = raw.partition("|")
        return datetime.fromisoformat(stamp), UUID(ident)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidInputError("некорректный курсор", code="ugc.cursor_invalid") from exc


class ListUgcQueue:
    """Страница очереди: старые заявки первыми, курсор по ``(submitted_at, id)``."""

    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self._uow = uow

    async def execute(
        self, *, status: UgcStatus, purpose: str | None, cursor: str | None, limit: int
    ) -> UgcPage:
        """Прочитать страницу (без блокировок)."""
        after = decode_cursor(cursor) if cursor else None
        async with self._uow() as uow:
            rows = await uow.ugc.queue(status, purpose, after=after, limit=limit + 1)
        page = rows[:limit]
        next_cursor = (
            encode_cursor(page[-1].submitted_at, page[-1].id) if len(rows) > limit else None
        )
        return UgcPage([UgcImageView.of(i) for i in page], next_cursor)
