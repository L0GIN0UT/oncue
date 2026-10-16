"""Общее для роутеров library: use-cases и ``Cache-Control: no-store`` на всех ответах."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute

from gv_core.errors import AppError
from gv_service.problems import problem, retry_after_seconds, status_for
from library_service.application.commands.save import LibraryCommands
from library_service.application.queries import LibraryQueries

NO_STORE = "no-store"


@dataclass(frozen=True, slots=True)
class ApiServices:
    """Use-cases для роутеров (собирает ``main.py``: слой api не знает infrastructure)."""

    service_name: str
    commands: LibraryCommands
    queries: LibraryQueries


class NoStoreRoute(APIRoute):
    """Маршрут, чьи ответы (и ошибки ``AppError``) не кэшируются: медиатека личная.

    Ошибки рендерятся здесь же тем же ``gv_service.problems``: обработчик исключений
    приложения создаёт новый ответ, и заголовок роутера на него не попал бы.
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
