"""Публичный API (/v1). На gateway: /api/rating/v1/....

Шаг 0a этапа 4b: контракт и заглушки (501 ``common.not_implemented`` после авторизации);
реализация — поток G (``docs/plans/stage-4.md`` §11, ADR 0037, 0039).

* ladder-ы, лидерборд и топ недели — публичные (кэш 10 с на bff); ``around=me`` и строка
  ``me`` — только с токеном пользователя;
* ``me/*`` и ``matches/{id}/changes`` — только пользователь (гость — 403
  ``auth.guest_forbidden``), ответы ``private, no-store``;
* пока игрок в калибровке, рейтинг и звание ``null`` (скрыты), виден ``calibration_left``.
"""

# Заглушки 0a этапа 4b: аргументы нужны схеме OpenAPI, тела придут в потоке G.
# ruff: noqa: ARG001, PLR0917
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from gv_auth import Principal, optional_principal, require_user
from gv_core.errors import NotImplementedYetError
from gv_service import Infrastructure, problem_responses
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

User = Annotated[Principal, Depends(require_user)]
Viewer = Annotated[Principal | None, Depends(optional_principal)]
LadderPath = Annotated[LadderKey, Path(description="Ключ ladder-а (`music`, `film`)")]
UserIdPath = Annotated[UUID, Path(description="Идентификатор пользователя")]
MatchIdPath = Annotated[UUID, Path(description="Идентификатор матча")]
Limit = Annotated[int, Query(ge=1, le=100)]
_STUB = "Заглушка контракта 0a этапа 4b: реализация — поток G (rating)"


def build_public_router(infra: Infrastructure) -> APIRouter:
    """Публичные ручки."""
    router = APIRouter(prefix="/v1", tags=["rating"])

    @router.get("/meta", response_model=ServiceMeta, summary="Сведения о сервисе")
    async def meta() -> ServiceMeta:
        return ServiceMeta(service=infra.settings.service_name, status="stub")

    @router.get(
        "/ladders",
        response_model=LadderList,
        summary="Ladder-ы",
        description=(
            "Опубликованные ladder-ы с действующим сезоном, званиями и длиной калибровки "
            "(всё из конфига ladder-а). Публично, кэшируется."
        ),
        responses=problem_responses(501),
    )
    async def ladders() -> LadderList:
        raise NotImplementedYetError(_STUB)

    @router.get(
        "/ladders/{ladder}/leaderboard",
        response_model=LeaderboardPage,
        summary="Лидерборд ladder-а",
        description=(
            "Сезонный лидерборд: keyset-курсор или `around=me` (окно вокруг пользователя, "
            "нужен токен — иначе 401; в калибровке — 404 `rating.not_ranked`). `season` — "
            "прошлый сезон по `SeasonFinished.standings_ref`; без него — текущий. Ошибки: 404 "
            "`rating.ladder_not_found`, 404 `rating.season_not_found`. Публично, кэш 10 с."
        ),
        responses=problem_responses(401, 404, 422, 501),
    )
    async def leaderboard(
        ladder: LadderPath,
        viewer: Viewer,
        cursor: Annotated[Cursor | None, Query()] = None,
        around: Annotated[Literal["me"] | None, Query()] = None,
        season: Annotated[UUID | None, Query()] = None,
        limit: Limit = 50,
    ) -> LeaderboardPage:
        raise NotImplementedYetError(_STUB)

    @router.get(
        "/ladders/{ladder}/weekly",
        response_model=WeeklyTop,
        summary="Топ недели",
        description=(
            "Прирост рейтинга с понедельника (зона и размер — из конфига ladder-а, ADR 0039). "
            "Ошибки: 404 `rating.ladder_not_found`. Публично, кэш 10 с."
        ),
        responses=problem_responses(404, 422, 501),
    )
    async def weekly(ladder: LadderPath, viewer: Viewer) -> WeeklyTop:
        raise NotImplementedYetError(_STUB)

    @router.get(
        "/me/ratings",
        response_model=MyRatings,
        summary="Мои рейтинги",
        description="Рейтинг, звание и калибровка по каждому ladder-у в текущем сезоне.",
        responses=problem_responses(401, 403, 501),
    )
    async def my_ratings(user: User) -> MyRatings:
        raise NotImplementedYetError(_STUB)

    @router.get(
        "/users/{user_id}/ratings",
        response_model=MyRatings,
        summary="Рейтинги пользователя",
        description=(
            "Публичный профиль: звания и места; в калибровке числа скрыты. Ошибки: 404 "
            "`rating.user_not_found` (нет или удалён)."
        ),
        responses=problem_responses(401, 403, 404, 422, 501),
    )
    async def user_ratings(user_id: UserIdPath, user: User) -> MyRatings:
        raise NotImplementedYetError(_STUB)

    @router.get(
        "/me/ratings/{ladder}/history",
        response_model=HistoryPage,
        summary="История рейтинга",
        description="Изменения рейтинга за матчи, новые первыми (keyset-курсор).",
        responses=problem_responses(401, 403, 404, 422, 501),
    )
    async def history(
        ladder: LadderPath,
        user: User,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Limit = 20,
    ) -> HistoryPage:
        raise NotImplementedYetError(_STUB)

    @router.get(
        "/matches/{match_id}/changes",
        response_model=MatchChanges,
        summary="Дельты матча",
        description=(
            "Изменения рейтинга игроков матча для экрана итога; только участник. Пока событие "
            "`MatchFinished` не обработано — 404 `rating.match_not_rated` (клиент опрашивает до "
            "10 с); не рейтинговый матч — тоже 404. В калибровке числа `null`."
        ),
        responses=problem_responses(401, 403, 404, 422, 501),
    )
    async def match_changes(match_id: MatchIdPath, user: User) -> MatchChanges:
        raise NotImplementedYetError(_STUB)

    return router
