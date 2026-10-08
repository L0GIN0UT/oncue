"""Middleware контекста запроса: correlation-id на входе и в ответе."""

from __future__ import annotations

import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from gv_core.context import RequestContext, bind_context
from gv_core.ids import uuid7
from gv_service.problems import problem

log = structlog.get_logger(__name__)

REQUEST_ID_HEADER = "x-request-id"


def _parse_request_id(raw: bytes | None) -> uuid.UUID:
    """Принять входящий id только если это корректный UUID — иначе сгенерировать свой.

    Произвольную строку из заголовка в логи и события не пропускаем: защита от
    log-injection и мусора в данных.
    """
    if raw:
        try:
            return uuid.UUID(raw.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            pass
    return uuid7()


class RequestContextMiddleware:
    """Связывает HTTP-запрос с ``RequestContext`` и возвращает ``X-Request-Id``."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = dict(scope["headers"]).get(REQUEST_ID_HEADER.encode())
        correlation_id = _parse_request_id(raw)

        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, str(correlation_id))
            await send(message)

        with bind_context(RequestContext(correlation_id=correlation_id)):
            try:
                await self.app(scope, receive, send_wrapper)
            except Exception:
                # Обработчик Exception у FastAPI живёт во внешнем ServerErrorMiddleware —
                # вне контекста запроса. Ловим здесь, чтобы и лог, и ответ 500 несли
                # correlation_id. Если ответ уже начат, исправлять нечего — пробрасываем.
                if started:
                    raise
                log.exception("request.unhandled_error")
                response = problem(500, "internal.error", "Внутренняя ошибка сервиса")
                await response(scope, receive, send_wrapper)
