"""Ручки заданий, бейджей и колеса: ``me/quests``, ``me/badges``, ``wheel``, ``wheel/spin`` (P2).

Только пользователь с аккаунтом (гость - 403), ответы ``private, no-store``. Тело ``wheel/spin``
пустое: любое непустое тело - 422 (сектор и приз клиент задать не может, ADR 0044).
"""

# Аргумент user — зависимость FastAPI ради проверки доступа.
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from gv_auth import Principal, require_user
from gv_core.errors import InvalidInputError
from gv_service import problem_responses
from progression_service.api.public.schemas import (
    BadgeView,
    MyBadges,
    MyQuests,
    PrizeView,
    QuestPeriodView,
    QuestView,
    Reward,
    SpinResult,
    WheelSectorView,
    WheelView,
)
from progression_service.application.badges import BadgeQueries
from progression_service.application.ports.rewards import SpinRecord
from progression_service.application.quests import PeriodQuests, QuestQueries
from progression_service.application.wheel import WheelService
from progression_service.domain.config import PrizeDef

User = Annotated[Principal, Depends(require_user)]
PRIVATE = "private, no-store"
SPIN_BODY_NOT_ALLOWED = "progression.spin_body_not_allowed"


def _prize(prize: PrizeDef) -> PrizeView:
    return PrizeView(
        kind=prize.kind.value,
        amount=prize.amount,
        multiplier=None if prize.multiplier is None else float(prize.multiplier),
        matches=prize.matches,
        item_key=prize.item_key,
    )


def _spin(record: SpinRecord, *, repeated: bool) -> SpinResult:
    return SpinResult(
        day=record.day,
        sector_key=record.sector_key,
        prize=_prize(record.prize),
        spun_at=record.spun_at,
        repeated=repeated,
    )


def _period(view: PeriodQuests) -> QuestPeriodView:
    return QuestPeriodView(
        period=view.period,
        period_start=view.period_start,
        period_end=view.period_end,
        items=[
            QuestView(
                key=a.snapshot.key,
                title=a.snapshot.title,
                description=a.snapshot.description,
                target=a.snapshot.target,
                progress=a.progress,
                completed=a.completed_at is not None,
                reward=Reward(xp=a.snapshot.reward.xp, verses=a.snapshot.reward.verses),
            )
            for a in view.items
        ],
    )


def register_reward_routes(
    router: APIRouter, quests: QuestQueries, badges: BadgeQueries, wheel_service: WheelService
) -> None:
    """Подключить ручки заданий, бейджей и колеса к роутеру ``/v1``."""

    @router.get(
        "/me/quests",
        response_model=MyQuests,
        summary="Мои задания",
        description=(
            "Дневные, недельные и разовые задания с прогрессом; назначение — снимком на период "
            "(смена конфига действует со следующего периода). "
            "Ошибки: 503 `progression.config_unavailable`."
        ),
        responses=problem_responses(401, 403, 503),
    )
    async def my_quests(user: User, response: Response) -> MyQuests:
        response.headers["Cache-Control"] = PRIVATE
        view = await quests.my_quests(user.user_id)
        return MyQuests(
            daily=_period(view.daily), weekly=_period(view.weekly), one_time=_period(view.one_time)
        )

    @router.get(
        "/me/badges",
        response_model=MyBadges,
        summary="Мои бейджи",
        description="Все бейджи конфига: полученные и с прогрессом.",
        responses=problem_responses(401, 403, 503),
    )
    async def my_badges(user: User, response: Response) -> MyBadges:
        response.headers["Cache-Control"] = PRIVATE
        return MyBadges(
            items=[
                BadgeView(
                    key=b.key,
                    title=b.title,
                    description=b.description,
                    target=b.target,
                    progress=b.progress,
                    unlocked_at=b.unlocked_at,
                )
                for b in await badges.my_badges(user.user_id)
            ]
        )

    @router.get(
        "/wheel",
        response_model=WheelView,
        summary="Колесо",
        description="Секторы с весами и шансами, спин за сегодня и время обновления.",
        responses=problem_responses(401, 403, 503),
    )
    async def wheel(user: User, response: Response) -> WheelView:
        response.headers["Cache-Control"] = PRIVATE
        state = await wheel_service.view(user.user_id)
        return WheelView(
            enabled=state.enabled,
            sectors=[
                WheelSectorView(
                    key=s.sector.key,
                    title=s.sector.title,
                    weight=s.sector.weight,
                    chance=s.chance,
                    prize=_prize(s.sector.prize),
                )
                for s in state.sectors
            ],
            day=state.day,
            today=None if state.today is None else _spin(state.today, repeated=True),
            next_spin_at=state.next_spin_at,
            config_version=state.config_version,
        )

    @router.post(
        "/wheel/spin",
        response_model=SpinResult,
        summary="Крутить колесо",
        description=(
            "Тело пустое (любое тело — 422). Один спин в сутки (`progression.timezone`): сектор "
            "выбирает сервер по весам, приз выдаётся в той же транзакции. Повтор за те же сутки "
            "возвращает тот же результат (`repeated = true`). Ошибки: 409 "
            "`progression.wheel_disabled`."
        ),
        responses=problem_responses(401, 403, 409, 422, 503),
    )
    async def spin(user: User, request: Request, response: Response) -> SpinResult:
        response.headers["Cache-Control"] = PRIVATE
        if (await request.body()).strip():
            raise InvalidInputError("тело запроса должно быть пустым", code=SPIN_BODY_NOT_ALLOWED)
        outcome = await wheel_service.spin(user.user_id)
        return _spin(outcome.record, repeated=outcome.repeated)
