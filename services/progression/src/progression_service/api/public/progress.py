"""Ручки прогресса: ``GET /v1/me/progress`` и ``GET /v1/users/{user_id}/progress`` (поток P1)."""

# Аргумент user — зависимость FastAPI ради проверки доступа.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response

from gv_auth import Principal, require_user
from gv_service import problem_responses
from progression_service.api.public.schemas import (
    BadgeRef,
    BoostView,
    DailyStreakView,
    MyProgress,
    PublicProgress,
    SessionRewards,
    SessionStreakView,
)
from progression_service.application.queries import ProgressQueries

User = Annotated[Principal, Depends(require_user)]
UserIdPath = Annotated[UUID, Path(description="Идентификатор пользователя")]
PRIVATE = "private, no-store"
PUBLIC_PROFILE = "private, max-age=60"


def register_progress_routes(router: APIRouter, queries: ProgressQueries) -> None:
    """Подключить ручки прогресса к роутеру ``/v1``."""

    @router.get(
        "/me/progress",
        response_model=MyProgress,
        summary="Мой прогресс",
        description=(
            "Уровень, опыт, потолок соло, серии дейлика по доменам. "
            "Ошибки: 503 `progression.config_unavailable` — нет опубликованного конфига."
        ),
        responses=problem_responses(401, 403, 503),
    )
    async def my_progress(user: User, response: Response) -> MyProgress:
        response.headers["Cache-Control"] = PRIVATE
        view = await queries.my_progress(user.user_id)
        return MyProgress(
            level=view.level,
            max_level=view.max_level,
            total_xp=view.total_xp,
            xp_in_level=view.xp_in_level,
            xp_to_next=view.xp_to_next,
            solo_xp_today=view.solo_xp_today,
            solo_xp_cap=view.solo_xp_cap,
            streaks=[
                DailyStreakView.model_validate(
                    {
                        "domain": s.domain,
                        "current": s.current,
                        "best": s.best,
                        "last_day": s.last_day,
                    }
                )
                for s in view.streaks
            ],
            boost=(
                None
                if view.boost.multiplier is None
                else BoostView(
                    multiplier=float(view.boost.multiplier), matches_left=view.boost.matches_left
                )
            ),
            boosts_queued=view.boost.queued,
            config_version=view.config_version,
        )

    @router.get(
        "/me/rewards",
        response_model=SessionRewards,
        summary="Награды за мою игровую сессию",
        description=(
            "Опыт, Версы, уровень до/после и серия дейлика за сессию `session_id` (соло или "
            "дейлик) — только текущего пользователя, по журналам начислений. Начисление "
            "асинхронное: до него (а также за чужую или несуществующую сессию, или если "
            "потолок съел всё) ответ 200 с `settled = false` и нулями — это не ошибка и не "
            "раскрывает существование чужих сессий. Только чтение, в ledger ничего не пишется."
        ),
        responses=problem_responses(401, 403, 422),
    )
    async def my_rewards(
        user: User,
        response: Response,
        session_id: Annotated[UUID, Query(description="Идентификатор игровой сессии")],
    ) -> SessionRewards:
        response.headers["Cache-Control"] = PRIVATE
        view = await queries.session_rewards(user.user_id, session_id)
        return SessionRewards(
            session_id=view.session_id,
            settled=view.settled,
            xp=view.xp,
            verses=view.verses,
            level_before=view.level_before,
            level_after=view.level_after,
            streak=(
                None
                if view.streak is None
                else SessionStreakView.model_validate(
                    {"domain": view.streak.domain, "current": view.streak.current}
                )
            ),
        )

    @router.get(
        "/users/{user_id}/progress",
        response_model=PublicProgress,
        summary="Прогресс игрока",
        description=(
            "Публичный профиль: уровень, опыт, бейджи. Ошибки: 404 `progression.user_not_found`."
        ),
        responses=problem_responses(401, 403, 404, 422),
    )
    async def user_progress(user_id: UserIdPath, user: User, response: Response) -> PublicProgress:
        response.headers["Cache-Control"] = PUBLIC_PROFILE
        view = await queries.public_progress(user_id)
        return PublicProgress(
            user_id=view.user_id,
            level=view.level,
            total_xp=view.total_xp,
            badges=[
                BadgeRef.model_validate(
                    {"key": b.key, "title": b.title, "unlocked_at": b.unlocked_at}
                )
                for b in view.badges
            ],
        )
