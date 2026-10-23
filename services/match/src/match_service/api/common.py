"""Общее для роутеров match: ``Cache-Control: no-store``, субъект команды, use-cases."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Protocol

from fastapi import Path, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from gv_auth import Principal, Sanction
from gv_core.errors import AppError
from gv_service.problems import problem, retry_after_seconds, status_for
from match_service.api.public.schemas import ROOM_CODE_PATTERN
from match_service.application.commands.queue import QueueService
from match_service.application.commands.rooms import Actor, CommandOutcome, RoomService
from match_service.application.queries.matches import MatchQueries, RatePlayer
from match_service.application.queries.rooms import RoomQueries

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


class MatchServices(Protocol):
    """Use-cases для роутеров (собирает ``main.py``: слой api не знает infrastructure)."""

    @property
    def service_name(self) -> str:
        """Имя сервиса."""
        ...

    @property
    def rooms(self) -> RoomService:
        """Команды комнат."""
        ...

    @property
    def room_queries(self) -> RoomQueries:
        """Чтение комнат."""
        ...

    @property
    def queue(self) -> QueueService:
        """Очередь рейтинговой дуэли."""
        ...

    @property
    def matches(self) -> MatchQueries:
        """История матчей."""
        ...

    @property
    def rate(self) -> RatePlayer:
        """Оценки игроков."""
        ...


ServicesProvider = Callable[[], MatchServices]
"""Ленивое получение use-cases: роутеры собираются без подключений (экспорт OpenAPI)."""


def actor_of(principal: Principal, request: Request | None = None) -> Actor:
    """Субъект команды: пользователь, санкции чата из claim, IP (лимит перебора кодов)."""
    ip = request.client.host if request is not None and request.client else None
    return Actor(
        user_id=principal.user_id,
        chat_muted=principal.is_sanctioned(Sanction.CHAT_MUTE),
        chat_slow=principal.is_sanctioned(Sanction.CHAT_SLOW),
        ranked_banned=principal.is_sanctioned(Sanction.RANKED),
        ip=ip,
    )


def respond[M: BaseModel](outcome: CommandOutcome, model: type[M]) -> M | JSONResponse:
    """200 — тело по схеме ответа (лишние поля отбрасываются); 202 — ``CommandAccepted``."""
    if outcome.status == status.HTTP_202_ACCEPTED:
        return JSONResponse(outcome.body, status_code=status.HTTP_202_ACCEPTED)
    return model.model_validate(outcome.body)
