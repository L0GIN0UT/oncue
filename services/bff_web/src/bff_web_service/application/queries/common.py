"""Общее для запросов экранов: ограничение параллелизма, блоки и перевод отказов источников."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import structlog

from bff_web_service.application.ports import UpstreamRejectedError, UpstreamUnavailableError
from bff_web_service.domain import Block
from gv_core.errors import (
    AppError,
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    UnauthenticatedError,
)

log = structlog.get_logger(__name__)

GUEST_FORBIDDEN = "auth.guest_forbidden"
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_UNPROCESSABLE = 422

type UpstreamError = UpstreamUnavailableError | UpstreamRejectedError


class Limiter:
    """Ограничение одновременных вызовов источников в рамках одного запроса экрана."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("limit >= 1")
        self._semaphore = asyncio.Semaphore(limit)

    async def run[T](self, call: Awaitable[T]) -> T:
        """Выполнить вызов, когда освободится слот."""
        async with self._semaphore:
            return await call

    async def attempt[T](self, call: Awaitable[T]) -> T | UpstreamError:
        """Выполнить вызов; ошибка источника возвращается значением (для ``gather``)."""
        try:
            return await self.run(call)
        except (UpstreamUnavailableError, UpstreamRejectedError) as exc:
            return exc


ANONYMOUS_SKIP = UpstreamRejectedError("game", HTTP_UNAUTHORIZED, "auth.unauthenticated")
"""Маркер «личный вызов не делался: зритель — аноним» (блок ``unavailable`` без лога)."""


def block_of[T](value: T | UpstreamError, block: str) -> Block[T]:
    """Значение источника → блок; любая ошибка — ``unavailable`` (с записью в лог)."""
    if value is ANONYMOUS_SKIP:
        return Block.unavailable()
    if isinstance(value, UpstreamUnavailableError | UpstreamRejectedError):
        log.warning(
            "bff.block_unavailable",
            block=block,
            service=value.service,
            reason=value.reason if isinstance(value, UpstreamUnavailableError) else value.code,
        )
        return Block.unavailable()
    return Block.ok(value)


def screen_error(exc: UpstreamRejectedError, *, not_found: str) -> AppError:
    """Осознанный отказ источника → ответ экрана с кодом источника.

    404 и прочие 4xx становятся 404 (``not_found`` — сообщение): не раскрываем, почему
    ресурса нет (IDOR). 422 — ошибка входа (например, курсор от другой сортировки).
    """
    if exc.status == HTTP_UNAUTHORIZED:
        return UnauthenticatedError("источник не принял токен", code=exc.code)
    if exc.status == HTTP_FORBIDDEN:
        return ForbiddenError("источник отказал в доступе", code=exc.code)
    if exc.status == HTTP_CONFLICT:
        return ConflictError("источник отклонил запрос", code=exc.code)
    if exc.status == HTTP_UNPROCESSABLE:
        return InvalidInputError("источник отклонил параметры", code=exc.code)
    code = exc.code if exc.status == HTTP_NOT_FOUND else NotFoundError.default_code
    return NotFoundError(not_found, code=code)
