"""Общее для роутеров game: ``Cache-Control: no-store`` на всех ответах, субъект игры."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute

from game_service.application.commands.daily import DailySetAdmin
from game_service.application.commands.modes import ModeConfigCommands, ModeConfigQueries
from game_service.application.commands.play import Player, PlayService
from game_service.application.queries.play import PlayQueries
from game_service.domain.session import PlayerKind, PlayerRef
from gv_auth import Principal
from gv_core.errors import AppError
from gv_service.problems import problem, retry_after_seconds, status_for

NO_STORE = "no-store"


@dataclass(frozen=True, slots=True)
class ApiServices:
    """Use-cases для роутеров (собирает ``main.py``: слой api не знает infrastructure)."""

    service_name: str
    play: PlayService
    queries: PlayQueries
    mode_commands: ModeConfigCommands
    mode_queries: ModeConfigQueries
    daily_admin: DailySetAdmin
    editor_schema: Callable[[], dict[str, Any]]


class NoStoreRoute(APIRoute):
    """Маршрут, чьи ответы (и ошибки ``AppError``) не кэшируются (§9 п. 1).

    Ошибки рендерятся здесь же тем же ``gv_service.problems``: обработчик исключений
    приложения создаёт новый ответ, и заголовок роутера на него не попал бы — а в
    ``details`` ошибки ``game.desync`` лежит снимок сессии.
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


def player_of(principal: Principal, request: Request | None = None) -> Player:
    """Игрок по субъекту токена: пользователь или гость (D5)."""
    kind = PlayerKind.GUEST if principal.is_guest else PlayerKind.USER
    ip = request.client.host if request is not None and request.client else None
    return Player(PlayerRef(kind, principal.user_id), ip)
