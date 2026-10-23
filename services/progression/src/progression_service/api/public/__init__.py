"""Публичный API (/v1). На gateway: /api/progression/v1/....

Контракт этапа 5a (``docs/plans/stage-5.md`` §2.4, ADR 0042–0044). Шаг 0a: ручки объявлены,
реализация — потоки P1 (``me/progress``, ``me/wallet``, ``users/{id}/progress``: модули
``progress.py`` и ``wallet.py``) и P2 (задания, бейджи, колесо); до тех пор последние отвечают
501 ``common.not_implemented``.

* все ручки — только пользователь с аккаунтом (гость — 403 ``auth.guest_forbidden``);
* ответы с данными игрока — ``private, no-store``;
* ``POST /wheel/spin`` — пустое тело; идемпотентен по ``(user_id, день)``, повтор возвращает
  тот же результат с ``repeated = true``.
"""

# Аргумент user — зависимость FastAPI ради проверки доступа.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from gv_auth import Principal, require_user
from gv_core.errors import NotImplementedYetError
from gv_service import Infrastructure, problem_responses
from progression_service.api.public.progress import register_progress_routes
from progression_service.api.public.schemas import (
    MyBadges,
    MyQuests,
    ServiceMeta,
    SpinResult,
    WheelView,
)
from progression_service.api.public.wallet import register_wallet_routes
from progression_service.application.queries import ProgressQueries

User = Annotated[Principal, Depends(require_user)]
_READ = problem_responses(401, 403, 501)


def _stub() -> NotImplementedYetError:
    return NotImplementedYetError("ручка появится в потоках P1/P2 этапа 5a")


def build_public_router(infra: Infrastructure, queries: ProgressQueries) -> APIRouter:
    """Публичные ручки."""
    router = APIRouter(prefix="/v1", tags=["progression"])

    @router.get("/meta", response_model=ServiceMeta, summary="Сведения о сервисе")
    async def meta() -> ServiceMeta:
        return ServiceMeta(service=infra.settings.service_name, status="stub")

    register_progress_routes(router, queries)
    register_wallet_routes(router, queries)

    @router.get(
        "/me/quests",
        response_model=MyQuests,
        summary="Мои задания",
        description=(
            "Дневные, недельные и разовые задания с прогрессом; назначение — снимком на период."
        ),
        responses=_READ,
    )
    async def my_quests(user: User) -> MyQuests:
        raise _stub()

    @router.get(
        "/me/badges",
        response_model=MyBadges,
        summary="Мои бейджи",
        description="Все бейджи конфига: полученные и с прогрессом.",
        responses=_READ,
    )
    async def my_badges(user: User) -> MyBadges:
        raise _stub()

    @router.get(
        "/wheel",
        response_model=WheelView,
        summary="Колесо",
        description="Секторы с весами и шансами, спин за сегодня и время обновления.",
        responses=_READ,
    )
    async def wheel(user: User) -> WheelView:
        raise _stub()

    @router.post(
        "/wheel/spin",
        response_model=SpinResult,
        summary="Крутить колесо",
        description=(
            "Тело пустое. Один спин в сутки (`progression.timezone`): сектор выбирает сервер "
            "по весам, приз выдаётся в той же транзакции. Повтор за те же сутки возвращает тот "
            "же результат (`repeated = true`). Ошибки: 409 `progression.wheel_disabled`."
        ),
        responses=problem_responses(401, 403, 409, 501),
    )
    async def spin(user: User) -> SpinResult:
        raise _stub()

    return router
