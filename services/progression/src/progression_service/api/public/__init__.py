"""Публичный API (/v1). На gateway: /api/progression/v1/....

Контракт этапа 5a (``docs/plans/stage-5.md`` §2.4, ADR 0042–0044): прогресс и кошелёк (P1:
``progress.py``, ``wallet.py``), задания, бейджи и колесо (P2: ``rewards.py``).

* все ручки — только пользователь с аккаунтом (гость — 403 ``auth.guest_forbidden``);
* ответы с данными игрока — ``private, no-store``;
* ``POST /wheel/spin`` — пустое тело; идемпотентен по ``(user_id, день)``, повтор возвращает
  тот же результат с ``repeated = true``.
"""

from __future__ import annotations

from fastapi import APIRouter

from gv_service import Infrastructure
from progression_service.api.public.progress import register_progress_routes
from progression_service.api.public.rewards import register_reward_routes
from progression_service.api.public.schemas import ServiceMeta
from progression_service.api.public.wallet import register_wallet_routes
from progression_service.application.badges import BadgeQueries
from progression_service.application.queries import ProgressQueries
from progression_service.application.quests import QuestQueries
from progression_service.application.wheel import WheelService


def build_public_router(
    infra: Infrastructure,
    queries: ProgressQueries,
    quests: QuestQueries,
    badges: BadgeQueries,
    wheel: WheelService,
) -> APIRouter:
    """Публичные ручки."""
    router = APIRouter(prefix="/v1", tags=["progression"])

    @router.get("/meta", response_model=ServiceMeta, summary="Сведения о сервисе")
    async def meta() -> ServiceMeta:
        return ServiceMeta(service=infra.settings.service_name, status="stub")

    register_progress_routes(router, queries)
    register_wallet_routes(router, queries)
    register_reward_routes(router, quests, badges, wheel)
    return router
