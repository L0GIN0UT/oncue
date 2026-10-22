"""Общее для роутеров match: ``Cache-Control: no-store`` на всех ответах, включая ошибки."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Path, Request, Response
from fastapi.routing import APIRoute

from gv_core.errors import AppError
from gv_service.problems import problem, retry_after_seconds, status_for
from match_service.api.public.schemas import ROOM_CODE_PATTERN

NO_STORE = "no-store"

RoomCodePath = Annotated[
    str,
    Path(
        pattern=ROOM_CODE_PATTERN,
        description="Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I)",
        examples=["K7MQ2ZXA"],
    ),
]


class NoStoreRoute(APIRoute):
    """Маршрут, чьи ответы (и ошибки ``AppError``) не кэшируются.

    Состояние комнаты личное и меняется каждую секунду; ошибки рендерятся здесь же, иначе
    обработчик исключений приложения создал бы ответ без заголовка роутера.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """Обёртка над обработчиком FastAPI."""
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await original(request)
            except AppError as exc:
                response = problem(
                    status_for(exc),
                    exc.code,
                    exc.message,
                    extra={"details": exc.details} if exc.details else None,
                    retry_after=retry_after_seconds(exc.details),
                )
            if "cache-control" not in response.headers:
                response.headers["Cache-Control"] = NO_STORE
            return response

        return handler
