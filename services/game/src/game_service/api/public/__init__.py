"""Публичный API (/v1). На gateway: /api/game/v1/....

Мутации — cookie + CSRF (``current_principal`` проверяет CSRF для cookie) или Bearer; ходы
и старт — с ``Idempotency-Key``; ответы — ``Cache-Control: no-store`` (кроме ``/modes``).
Чужая или несуществующая сессия — 404 ``game.session_not_found`` (не 403).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from pydantic import BaseModel

from game_service.api.common import ApiServices, NoStoreRoute, player_of
from game_service.api.public.schemas import (
    ClueOut,
    ClueRequest,
    DailyStatus,
    Domain,
    GiveUpRequest,
    GuessRequest,
    ItemAnswer,
    ModeOut,
    MoveResult,
    SessionResult,
    SessionSnapshot,
    SkipRequest,
    StartSessionRequest,
)
from gv_auth import Principal, current_principal
from gv_service import IdempotencyKey, problem_responses

Subject = Annotated[Principal, Depends(current_principal)]
SessionId = Annotated[UUID, Path(description="Идентификатор сессии")]
RoundIndex = Annotated[int, Path(ge=0, le=49, description="Номер раунда с 0")]
MODES_CACHE = "public, max-age=30"


class ServiceMeta(BaseModel):
    """Сведения о сервисе."""

    service: str
    status: str


def build_public_router(c: ApiServices) -> APIRouter:
    """Публичные ручки."""
    router = APIRouter(prefix="/v1", tags=["game"], route_class=NoStoreRoute)

    @router.get("/meta", response_model=ServiceMeta, summary="Сведения о сервисе")
    async def meta() -> ServiceMeta:
        return ServiceMeta(service=c.service_name, status="ok")

    @router.get(
        "/modes",
        response_model=list[ModeOut],
        summary="Опубликованные режимы",
        description="Публичный, `Cache-Control: public, max-age=30`.",
        responses=problem_responses(422, 503),
    )
    async def list_modes(
        response: Response, domain: Annotated[Domain | None, Query()] = None
    ) -> list[ModeOut]:
        response.headers["Cache-Control"] = MODES_CACHE
        return [ModeOut.model_validate(m) for m in await c.queries.modes(domain)]

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
            **problem_responses(401, 403, 404, 409, 422, 429, 503),
        },
    )
    async def start_session(
        body: StartSessionRequest,
        subject: Subject,
        key: IdempotencyKey,
        request: Request,
        response: Response,
    ) -> SessionSnapshot:
        snapshot, created = await c.play.start(
            player_of(subject, request), body.mode_key, body.preset_id, key
        )
        if not created:
            response.status_code = status.HTTP_200_OK
        return SessionSnapshot.model_validate(snapshot)

    @router.get(
        "/sessions/{session_id}",
        response_model=SessionSnapshot,
        summary="Снимок сессии",
        responses=problem_responses(401, 404, 503),
    )
    async def get_session(session_id: SessionId, subject: Subject) -> SessionSnapshot:
        return SessionSnapshot.model_validate(await c.play.snapshot(player_of(subject), session_id))

    @router.post(
        "/sessions/{session_id}/rounds/{index}/clue",
        response_model=ClueOut,
        summary="Ссылка на ассет уровня",
        description=(
            "POST: каждая выдача пишется в журнал media-pipeline и ограничена лимитом. "
            "Ошибки: 403 `game.level_locked`, 410 `game.asset_unavailable` (раунд становится "
            "`void`), 429, 503 `game.assets_unavailable`."
        ),
        responses=problem_responses(401, 403, 404, 409, 410, 422, 429, 503),
    )
    async def issue_clue(
        session_id: SessionId, index: RoundIndex, body: ClueRequest, subject: Subject
    ) -> ClueOut:
        clue = await c.play.issue_clue(player_of(subject), session_id, index, body.level)
        return ClueOut.model_validate(clue)

    @router.post(
        "/sessions/{session_id}/guesses",
        response_model=MoveResult,
        summary="Ответ",
        description=(
            "Ошибки: 409 `game.desync` (`details.session` — актуальный `SessionSnapshot`), "
            "409 `game.round_finished`, 429."
        ),
        responses=problem_responses(401, 404, 409, 422, 429, 503),
    )
    async def submit_guess(
        session_id: SessionId, body: GuessRequest, subject: Subject, key: IdempotencyKey
    ) -> MoveResult:
        item = body.answer.media_item_id if isinstance(body.answer, ItemAnswer) else None
        text = None if isinstance(body.answer, ItemAnswer) else body.answer.text
        result = await c.play.guess(
            player_of(subject),
            session_id,
            round_index=body.round_index,
            expected_version=body.expected_version,
            media_item_id=item,
            text=text,
            idempotency_key=key,
        )
        return MoveResult.model_validate(result)

    @router.post(
        "/sessions/{session_id}/skips",
        response_model=MoveResult,
        summary="Пропустить уровень",
        description=(
            "Ошибки: 409 `game.desync` (`details.session` — актуальный `SessionSnapshot`), "
            "409 `game.round_finished`, 429."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 429, 503),
    )
    async def skip_level(
        session_id: SessionId, body: SkipRequest, subject: Subject, key: IdempotencyKey
    ) -> MoveResult:
        result = await c.play.skip(
            player_of(subject),
            session_id,
            round_index=body.round_index,
            expected_version=body.expected_version,
            idempotency_key=key,
        )
        return MoveResult.model_validate(result)

    @router.post(
        "/sessions/{session_id}/rounds/{index}/give-up",
        response_model=MoveResult,
        summary="Сдаться в раунде",
        description=(
            "Ошибки: 409 `game.desync` (`details.session` — актуальный `SessionSnapshot`), "
            "409 `game.round_finished`, 429."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 429, 503),
    )
    async def give_up(
        session_id: SessionId,
        index: RoundIndex,
        body: GiveUpRequest,
        subject: Subject,
        key: IdempotencyKey,
    ) -> MoveResult:
        result = await c.play.give_up(
            player_of(subject),
            session_id,
            round_index=index,
            expected_version=body.expected_version,
            idempotency_key=key,
        )
        return MoveResult.model_validate(result)

    @router.get(
        "/sessions/{session_id}/result",
        response_model=SessionResult,
        summary="Итог завершённой сессии",
        responses=problem_responses(401, 404, 409, 503),
    )
    async def get_result(session_id: SessionId, subject: Subject) -> SessionResult:
        return SessionResult.model_validate(await c.queries.result(player_of(subject), session_id))

    @router.get(
        "/daily",
        response_model=list[DailyStatus],
        summary="Дейлики текущего субъекта",
        responses=problem_responses(401, 422),
    )
    async def daily_status(
        subject: Subject, domain: Annotated[Domain | None, Query()] = None
    ) -> list[DailyStatus]:
        return [
            DailyStatus.model_validate(d) for d in await c.queries.daily(player_of(subject), domain)
        ]

    return router
