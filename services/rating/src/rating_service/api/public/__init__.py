"""Публичный API (/v1). На gateway: /api/rating/v1/....

Реализация потока G этапа 4b (``docs/plans/stage-4.md`` §11, ADR 0037, 0039).

* ladder-ы, лидерборд и топ недели — публичные (кэш 10 с на bff); ``around=me`` и строка
  ``me`` — только с токеном пользователя (с токеном ответ ``private, no-store``);
* ``me/*`` и ``matches/{id}/changes`` — только пользователь (гость — 403
  ``auth.guest_forbidden``), ответы ``private, no-store``;
* пока игрок в калибровке, рейтинг и звание ``null`` (скрыты), виден ``calibration_left``.
"""

# Аргументы ручек — зависимости FastAPI, часть из них нужна только ради проверки доступа.
# ruff: noqa: ARG001, PLR0917
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response

from gv_auth import Principal, optional_principal, require_user
from gv_service import problem_responses
from rating_service.api.public.schemas import (
    Cursor,
    HistoryPage,
    LadderKey,
    LadderList,
    LeaderboardPage,
    MatchChanges,
    MyRatings,
    ServiceMeta,
    WeeklyTop,
)
from rating_service.application.queries import RatingQueries, ViewerKind

User = Annotated[Principal, Depends(require_user)]
Viewer = Annotated[Principal | None, Depends(optional_principal)]
LadderPath = Annotated[LadderKey, Path(description="Ключ ladder-а (`music`, `film`)")]
UserIdPath = Annotated[UUID, Path(description="Идентификатор пользователя")]
MatchIdPath = Annotated[UUID, Path(description="Идентификатор матча")]
Limit = Annotated[int, Query(ge=1, le=100)]

PRIVATE = "private, no-store"
PUBLIC = "public, max-age=10"


def _kind(viewer: Principal | None) -> ViewerKind:
    if viewer is None:
        return "anonymous"
    return "guest" if viewer.is_guest else "user"


def _cache(response: Response, viewer: Principal | None) -> None:
    """Публичные ручки: общий кэш 10 с без токена, ``private`` — если ответ зависит от токена."""
    response.headers["Cache-Control"] = PUBLIC if viewer is None else PRIVATE
    response.headers["Vary"] = "Authorization, Cookie"


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = PRIVATE


def build_public_router(service_name: str, queries: RatingQueries) -> APIRouter:
    """Публичные ручки."""
    router = APIRouter(prefix="/v1", tags=["rating"])

    @router.get("/meta", response_model=ServiceMeta, summary="Сведения о сервисе")
    async def meta() -> ServiceMeta:
        return ServiceMeta(service=service_name, status="ok")

    @router.get(
        "/ladders",
        response_model=LadderList,
        summary="Ladder-ы",
        description=(
            "Опубликованные ladder-ы с действующим сезоном, званиями и длиной калибровки "
            "(всё из конфига ladder-а). Публично, кэшируется."
        ),
        responses=problem_responses(401),
    )
    async def ladders(response: Response, viewer: Viewer) -> LadderList:
        _cache(response, viewer)
        return LadderList.model_validate(await queries.ladders())

    @router.get(
        "/ladders/{ladder}/leaderboard",
        response_model=LeaderboardPage,
        summary="Лидерборд ladder-а",
        description=(
            "Сезонный лидерборд: курсор или `around=me` (окно вокруг пользователя, "
            "нужен токен — иначе 401; в калибровке — 404 `rating.not_ranked`). `season` — "
            "прошлый сезон по `SeasonFinished.standings_ref`; без него — текущий. `limit` по "
            "умолчанию — `leaderboard.page_size` конфига. Ошибки: 404 "
            "`rating.ladder_not_found`, 404 `rating.season_not_found`. Публично, кэш 10 с."
        ),
        responses=problem_responses(401, 403, 404, 422),
    )
    async def leaderboard(
        ladder: LadderPath,
        response: Response,
        viewer: Viewer,
        cursor: Annotated[Cursor | None, Query()] = None,
        around: Annotated[Literal["me"] | None, Query()] = None,
        season: Annotated[UUID | None, Query()] = None,
        limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    ) -> LeaderboardPage:
        _cache(response, viewer)
        page = await queries.leaderboard(
            ladder,
            viewer=viewer.user_id if viewer else None,
            viewer_kind=_kind(viewer),
            cursor=cursor,
            around_me=around == "me",
            season_id=season,
            limit=limit,
        )
        return LeaderboardPage.model_validate(page)

    @router.get(
        "/ladders/{ladder}/weekly",
        response_model=WeeklyTop,
        summary="Топ недели",
        description=(
            "Прирост рейтинга с понедельника (зона и размер — из конфига ladder-а, ADR 0039). "
            "Ошибки: 404 `rating.ladder_not_found`. Публично, кэш 10 с."
        ),
        responses=problem_responses(401, 404, 422),
    )
    async def weekly(ladder: LadderPath, response: Response, viewer: Viewer) -> WeeklyTop:
        _cache(response, viewer)
        top = await queries.weekly(
            ladder, viewer=viewer.user_id if viewer else None, viewer_kind=_kind(viewer)
        )
        return WeeklyTop.model_validate(top)

    @router.get(
        "/me/ratings",
        response_model=MyRatings,
        summary="Мои рейтинги",
        description="Рейтинг, звание и калибровка по каждому ladder-у в текущем сезоне.",
        responses=problem_responses(401, 403),
    )
    async def my_ratings(response: Response, user: User) -> MyRatings:
        _private(response)
        return MyRatings.model_validate(await queries.my_ratings(user.user_id))

    @router.get(
        "/users/{user_id}/ratings",
        response_model=MyRatings,
        summary="Рейтинги пользователя",
        description=(
            "Публичный профиль: звания и места; в калибровке числа скрыты. Ошибки: 404 "
            "`rating.user_not_found` (нет или удалён)."
        ),
        responses=problem_responses(401, 403, 404, 422),
    )
    async def user_ratings(user_id: UserIdPath, response: Response, user: User) -> MyRatings:
        _private(response)
        return MyRatings.model_validate(await queries.user_ratings(user_id))

    @router.get(
        "/me/ratings/{ladder}/history",
        response_model=HistoryPage,
        summary="История рейтинга",
        description="Изменения рейтинга за матчи, новые первыми (курсор).",
        responses=problem_responses(401, 403, 404, 422),
    )
    async def history(
        ladder: LadderPath,
        response: Response,
        user: User,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Limit = 20,
    ) -> HistoryPage:
        _private(response)
        page = await queries.history(user.user_id, ladder, cursor=cursor, limit=limit)
        return HistoryPage.model_validate(page)

    @router.get(
        "/matches/{match_id}/changes",
        response_model=MatchChanges,
        summary="Дельты матча",
        description=(
            "Изменения рейтинга игроков матча для экрана итога; только участник. Пока событие "
            "`MatchFinished` не обработано — 404 `rating.match_not_rated` (клиент опрашивает до "
            "10 с); не рейтинговый матч и чужой матч — тоже 404. В калибровке числа `null`."
        ),
        responses=problem_responses(401, 403, 404, 422),
    )
    async def match_changes(match_id: MatchIdPath, response: Response, user: User) -> MatchChanges:
        _private(response)
        return MatchChanges.model_validate(await queries.match_changes(match_id, user.user_id))

    return router
