"""Публичный API (/v1). На gateway: /api/game/v1/....

Мутации — cookie + CSRF (``current_principal`` проверяет CSRF для cookie) или Bearer; ходы
и старт — с ``Idempotency-Key``; ответы — ``Cache-Control: no-store`` (кроме ``/modes``).
Чужая или несуществующая сессия — 404 ``game.session_not_found`` (не 403).
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from pydantic import BaseModel

from game_service.api.common import ApiServices, NoStoreRoute, player_of
from game_service.api.public.schemas import (
    MAX_ROUND_INDEX,
    ClueOut,
    ClueRequest,
    CollectionModeOut,
    DailyStatus,
    Domain,
    GiveUpRequest,
    GuessRequest,
    ItemAnswer,
    ModeKey,
    ModeOut,
    MoveResult,
    OptionAnswer,
    PlayabilityOut,
    PlayabilityRequest,
    RecordOut,
    SessionResult,
    SessionSnapshot,
    SkipRequest,
    StartSessionRequest,
    SubjectOut,
    TextAnswer,
)
from gv_auth import Principal, current_principal, optional_principal, require_user
from gv_service import IdempotencyKey, problem_responses

Subject = Annotated[Principal, Depends(current_principal)]
OptionalSubject = Annotated[Principal | None, Depends(optional_principal)]
User = Annotated[Principal, Depends(require_user)]
CollectionId = Annotated[UUID, Path(description="Идентификатор сборника")]
SessionId = Annotated[UUID, Path(description="Идентификатор сессии")]
RoundIndex = Annotated[int, Path(ge=0, le=MAX_ROUND_INDEX, description="Номер раунда с 0")]
PathModeKey = Annotated[ModeKey, Path(description="Ключ режима")]
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
        description=(
            "Публичный, `Cache-Control: public, max-age=30`. `kind=play` (по умолчанию) — "
            "режимы, которые играются сами по себе, без шаблонов сборника (source "
            "`collection`); `kind=collection` — только шаблоны сборника (конструктор, экран "
            "сессии по сборнику) с `round_options`/`min_items` из конфига. Играбельность "
            "конкретного сборника — `GET /v1/collections/{collection_id}/modes`. "
            "`session=solo` (по умолчанию) — только соло-режимы; `session=online` — "
            "онлайн-шаблоны (дуэль, party, лобби, в том числе по сборнику; `kind` не "
            "учитывается) с блоком `online` (ADR 0032): соло-хаб их не видит."
        ),
        responses=problem_responses(422, 503),
    )
    async def list_modes(
        response: Response,
        domain: Annotated[Domain | None, Query()] = None,
        kind: Annotated[Literal["play", "collection"], Query()] = "play",
        session: Annotated[Literal["solo", "online"], Query()] = "solo",
    ) -> list[ModeOut]:
        response.headers["Cache-Control"] = MODES_CACHE
        rows = await c.queries.modes(
            domain, collection=kind == "collection", online=session == "online"
        )
        return [ModeOut.model_validate(m) for m in rows]

    @router.post(
        "/sessions",
        response_model=SessionSnapshot,
        status_code=status.HTTP_201_CREATED,
        summary="Начать сессию",
        description=(
            "201 — новая сессия. Для дейлика при незавершённой сессии дня — 200 с ней же. "
            "Ошибки: 409 `game.daily_already_played` (`details.session_id`), "
            "403 `game.guest_not_allowed`, 404 `game.mode_not_found`, 422 "
            "`game.mode_not_solo` (онлайн-режим играется только в комнате match), "
            "503 `game.daily_not_ready` (набор периода не готов), 503 `game.pool_empty`. "
            "Марафон: `subject_ids` обязательны — 422 `game.subject_required`, "
            "`game.too_many_subjects`; режим без выбора персон — 422 "
            "`game.subjects_not_supported`. Сетка периода: повторный вход в доигранную — "
            "409 `game.daily_already_played`. Сборник (`collection_id`, `rounds`): 404 "
            "`game.collection_not_found` (нет, скрыт или удалён), 422 "
            "`game.collection_required` (режим с source `collection` без сборника), 422 "
            "`game.collection_domain_mismatch`, 422 `game.rounds_not_allowed` (нет в "
            "`round_options`), 422 `game.collection_not_supported` (`collection_id` у режима "
            "без source `collection`), 422 `game.pool_too_small`."
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
            player_of(subject, request),
            body.mode_key,
            body.preset_id,
            key,
            subject_ids=body.subject_ids or (),
            collection_id=body.collection_id,
            rounds=body.rounds,
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
            "Ответ — `{media_item_id}`, `{text}` (формат `classic`) или `{option_id}` "
            "(формат `pick`). Ошибки: 409 `game.desync` (`details.session` — актуальный "
            "`SessionSnapshot`), 409 `game.round_finished`, 422 "
            "`game.answer_kind_unsupported` (вид ответа не для формата режима), 422 "
            "`game.option_unknown`, 429. У сетки — `stats.agree_pct`."
        ),
        responses=problem_responses(401, 404, 409, 422, 429, 503),
    )
    async def submit_guess(
        session_id: SessionId, body: GuessRequest, subject: Subject, key: IdempotencyKey
    ) -> MoveResult:
        answer = body.answer
        result = await c.play.guess(
            player_of(subject),
            session_id,
            round_index=body.round_index,
            expected_version=body.expected_version,
            media_item_id=answer.media_item_id if isinstance(answer, ItemAnswer) else None,
            text=answer.text if isinstance(answer, TextAnswer) else None,
            option_id=answer.option_id if isinstance(answer, OptionAnswer) else None,
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
        "/modes/{mode_key}/subjects",
        response_model=list[SubjectOut],
        summary="Персоны для марафона",
        description=(
            "Поиск по имени среди персон с ролью режима и не меньше `min_items` "
            "играбельных единиц; по убыванию числа единиц. 422 "
            "`game.subjects_not_supported` — режим не выбирает персон; 429."
        ),
        responses=problem_responses(401, 404, 422, 429),
    )
    async def list_subjects(
        mode_key: PathModeKey,
        subject: Subject,
        q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
    ) -> list[SubjectOut]:
        rows = await c.queries.subjects(player_of(subject), mode_key, q, limit)
        return [SubjectOut.model_validate(r) for r in rows]

    @router.post(
        "/sessions/{session_id}/rounds/{index}/final-clue",
        response_model=ClueOut,
        summary="Свежая ссылка на ассет ревила",
        description=(
            "Только для завершённого не-void раунда своей сессии; ограничена лимитом. "
            "Ошибки: 409 `game.round_not_finished`, 410 `game.asset_unavailable`, 429."
        ),
        responses=problem_responses(401, 404, 409, 410, 422, 429, 503),
    )
    async def final_clue(session_id: SessionId, index: RoundIndex, subject: Subject) -> ClueOut:
        clue = await c.play.final_clue(player_of(subject), session_id, index)
        return ClueOut.model_validate(clue)

    @router.get(
        "/me/records",
        response_model=list[RecordOut],
        summary="Личные рекорды",
        description="Только для пользователей; гостю — пустой список.",
        responses=problem_responses(401, 422),
    )
    async def my_records(
        subject: Subject, mode_key: Annotated[ModeKey | None, Query()] = None
    ) -> list[RecordOut]:
        rows = await c.queries.records(player_of(subject), mode_key)
        return [RecordOut.model_validate(r) for r in rows]

    @router.get(
        "/collections/{collection_id}/modes",
        response_model=list[CollectionModeOut],
        summary="Режимы сборника",
        description=(
            "Опубликованные режимы с source `collection` домена сборника и сколько единиц "
            "в каждом играбельно. Только опубликованный, не скрытый сборник (по проекции "
            "`collections.collection.v1`), иначе 404 `game.collection_not_found`. Без "
            "названий и `media_item_id` (D6). Субъект не обязателен."
        ),
        responses=problem_responses(401, 404, 422, 503),
    )
    async def collection_modes(
        collection_id: CollectionId,
        subject: OptionalSubject,  # noqa: ARG001 — токен необязателен, но проверяется
    ) -> list[CollectionModeOut]:
        rows = await c.queries.collection_modes(collection_id)
        return [CollectionModeOut.model_validate(r) for r in rows]

    @router.post(
        "/modes/{mode_key}/playability",
        response_model=PlayabilityOut,
        summary="Играбельность единиц черновика сборника",
        description=(
            "Для конструктора: какие из ≤ 200 единиц играбельны в режиме (активны в "
            "каталоге и есть каталожный набор ассетов). Только пользователи (гость — 403 "
            "`auth.guest_forbidden`), лимит `GV_GAME_LIMITS__PLAYABILITY_PER_MIN` (429). "
            "Режим без source `collection` — 422 `game.collection_not_supported`."
        ),
        responses=problem_responses(401, 403, 404, 422, 429, 503),
    )
    async def playability(
        mode_key: PathModeKey, body: PlayabilityRequest, subject: User
    ) -> PlayabilityOut:
        result = await c.queries.playability(player_of(subject), mode_key, body.media_item_ids)
        return PlayabilityOut.model_validate(result)

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
