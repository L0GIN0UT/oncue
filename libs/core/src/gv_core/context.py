"""Контекст текущего запроса или сообщения.

Хранится в ``contextvars``, поэтому корректно работает с asyncio: у каждой задачи свой
контекст. Заполняется на входе (HTTP-middleware, consumer, worker) и читается логированием,
Unit of Work (``correlation_id`` для событий) и клиентами других сервисов.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Сквозные идентификаторы текущей операции.

    Attributes:
        correlation_id: Один на всю цепочку (HTTP-запрос → события → задачи).
        causation_id: Идентификатор события или запроса, непосредственно вызвавшего операцию.
        user_id: Пользователь-инициатор, если известен.
    """

    correlation_id: uuid.UUID
    causation_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None


_current: ContextVar[RequestContext | None] = ContextVar("gv_request_context", default=None)


def current_context() -> RequestContext | None:
    """Вернуть контекст текущей операции или ``None`` вне её."""
    return _current.get()


@contextmanager
def bind_context(ctx: RequestContext) -> Iterator[RequestContext]:
    """Установить контекст на время блока и восстановить предыдущий после."""
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


def attach_user(user_id: uuid.UUID) -> None:
    """Дописать ``user_id`` в контекст текущей операции.

    Вызывается после аутентификации внутри уже открытого ``bind_context`` (middleware).
    Значение живёт до выхода из внешнего блока: его ``reset`` восстановит прежний контекст.
    Вне операции ничего не делает.
    """
    ctx = _current.get()
    if ctx is not None and ctx.user_id is None:
        _current.set(RequestContext(ctx.correlation_id, ctx.causation_id, user_id))
