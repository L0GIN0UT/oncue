"""Публичный API (/v1). На gateway: /api/game/v1/....

Шаг 0a плана этапа 2 (§2.6): схемы и ручки-заглушки (501 ``common.not_implemented``);
реализация — поток B. Мутации — cookie + CSRF (``current_principal`` проверяет CSRF для
cookie) или Bearer; ходы — с ``Idempotency-Key``; ответы — ``Cache-Control: no-store``.
Чужая или несуществующая сессия — 404 ``game.session_not_found`` (не 403).
"""

# ruff: noqa: ARG001 — параметры заглушек объявлены ради контракта OpenAPI; снять в потоке B.

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import BaseModel

from game_service.api.common import not_implemented
from game_service.api.public.schemas import (
    ClueOut,
    ClueRequest,
    DailyStatus,
    Domain,
    GiveUpRequest,
    GuessRequest,
    ModeOut,
    MoveResult,
    SessionResult,
    SessionSnapshot,
    SkipRequest,
    StartSessionRequest,
)
from gv_auth import Principal, current_principal
from gv_service import IdempotencyKey, Infrastructure, problem_responses

Subject = Annotated[Principal, Depends(current_principal)]
SessionId = Annotated[UUID, Path(description="Идентификатор сессии")]
RoundIndex = Annotated[int, Path(ge=0, le=49, description="Номер раунда с 0")]
_NI = (501,)


class ServiceMeta(BaseModel):
    """Сведения о сервисе."""

    service: str
    status: str


def build_public_router(infra: Infrastructure) -> APIRouter:
    """Публичные ручки."""
    router = APIRouter(prefix="/v1", tags=["game"])

    @router.get("/meta", response_model=ServiceMeta, summary="Сведения о сервисе")
    async def meta() -> ServiceMeta:
        return ServiceMeta(service=infra.settings.service_name, status="stub")

    @router.get(
        "/modes",
        response_model=list[ModeOut],
        summary="Опубликованные режимы",
        description="Публичный, `Cache-Control: public, max-age=30`.",
        responses=problem_responses(422, *_NI),
    )
    async def list_modes(domain: Annotated[Domain | None, Query()] = None) -> list[ModeOut]:
        not_implemented()

    @router.post(
        "/sessions",
        response_model=SessionSnapshot,
        status_code=status.HTTP_201_CREATED,
        summary="Начать сессию",
        description=(
            "201 — новая сессия. Для дейлика при незавершённой сессии дня — 200 с ней же. "
            "Ошибки: 409 `game.daily_already_played` (`details.session_id`), "
            "403 `game.guest_not_allowed`, 404 `game.mode_not_found`, "
            "503 `game.daily_not_ready`."
        ),
        responses={
            200: {"model": SessionSnapshot, "description": "Незавершённая сессия дейлика"},
            **problem_responses(401, 403, 404, 409, 422, 429, 503, *_NI),
        },
    )
    async def start_session(
        body: StartSessionRequest, subject: Subject, key: IdempotencyKey
    ) -> SessionSnapshot:
        not_implemented()

    @router.get(
        "/sessions/{session_id}",
        response_model=SessionSnapshot,
        summary="Снимок сессии",
        responses=problem_responses(401, 404, *_NI),
    )
    async def get_session(session_id: SessionId, subject: Subject) -> SessionSnapshot:
        not_implemented()

    @router.post(
        "/sessions/{session_id}/rounds/{index}/clue",
        response_model=ClueOut,
        summary="Ссылка на ассет уровня",
        description=(
            "POST: каждая выдача пишется в журнал media-pipeline и ограничена лимитом. "
            "Ошибки: 403 `game.level_locked`, 410 `game.asset_unavailable` (раунд становится "
            "`void`), 429, 503 `game.assets_unavailable`."
        ),
        responses=problem_responses(401, 403, 404, 410, 422, 429, 503, *_NI),
    )
    async def issue_clue(
        session_id: SessionId, index: RoundIndex, body: ClueRequest, subject: Subject
    ) -> ClueOut:
        not_implemented()

    @router.post(
        "/sessions/{session_id}/guesses",
        response_model=MoveResult,
        summary="Ответ",
        description=(
            "Ошибки: 409 `game.desync` (в `details` — актуальный снимок), "
            "409 `game.round_finished`, 429."
        ),
        responses=problem_responses(401, 404, 409, 422, 429, *_NI),
    )
    async def submit_guess(
        session_id: SessionId, body: GuessRequest, subject: Subject, key: IdempotencyKey
    ) -> MoveResult:
        not_implemented()

    @router.post(
        "/sessions/{session_id}/skips",
        response_model=MoveResult,
        summary="Пропустить уровень",
        responses=problem_responses(401, 404, 409, 422, 429, *_NI),
    )
    async def skip_level(
        session_id: SessionId, body: SkipRequest, subject: Subject, key: IdempotencyKey
    ) -> MoveResult:
        not_implemented()

    @router.post(
        "/sessions/{session_id}/rounds/{index}/give-up",
        response_model=MoveResult,
        summary="Сдаться в раунде",
        responses=problem_responses(401, 404, 409, 422, 429, *_NI),
    )
    async def give_up(
        session_id: SessionId,
        index: RoundIndex,
        body: GiveUpRequest,
        subject: Subject,
        key: IdempotencyKey,
    ) -> MoveResult:
        not_implemented()

    @router.get(
        "/sessions/{session_id}/result",
        response_model=SessionResult,
        summary="Итог завершённой сессии",
        responses=problem_responses(401, 404, 409, *_NI),
    )
    async def get_result(session_id: SessionId, subject: Subject) -> SessionResult:
        not_implemented()

    @router.get(
        "/daily",
        response_model=list[DailyStatus],
        summary="Дейлики текущего субъекта",
        responses=problem_responses(401, 422, *_NI),
    )
    async def daily_status(
        subject: Subject, domain: Annotated[Domain | None, Query()] = None
    ) -> list[DailyStatus]:
        not_implemented()

    return router
