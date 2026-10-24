"""Admin API (/admin/v1): очередь модерации, конфиг и санкции (5c; ADR 0051-0053).

* очередь - `cases.py` (F4); конфиг (ADR 0053) - `config.py` (F4);
* игрок для модератора и санкции (`users/*`, `sanctions/*`) - поток F3, до его слияния 501;
  F3 выносит их в `sanctions.py` и регистрирует одной строкой в `build_admin_router`;
* права: очередь - `admin.access` + `social.moderate`; санкции `ranked|chat|online` -
  `social.sanctions.manage`, `account` - дополнительно `social.sanctions.account` (проверка в
  application-слое: 403 `social.account_sanction_forbidden`);
* мутации: обязательные `Idempotency-Key` и `reason` (в аудит), оптимистичная блокировка по
  `version` кейса (409 `social.case_version_conflict`);
* модератор не выдаёт и не снимает санкции себе (403 `social.self_sanction_forbidden`);
* успешные ответы - `Cache-Control: no-store`; текст жалоб не покидает админ-API.
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки права.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path

from gv_auth import Principal, require_perm
from gv_core.errors import NotImplementedYetError
from gv_service import IdempotencyKey, problem_responses
from social_service.api.admin.cases import register_case_routes
from social_service.api.admin.config import register_config_routes
from social_service.api.admin.services import AdminServices
from social_service.api.schemas import (
    SanctionGrant,
    SanctionRevoke,
    SanctionView,
    UserModeration,
)

Moderator = Annotated[Principal, Depends(require_perm("admin.access", "social.moderate"))]
Sanctioner = Annotated[Principal, Depends(require_perm("admin.access", "social.sanctions.manage"))]
UserIdPath = Annotated[UUID, Path(description="Идентификатор игрока")]
SanctionIdPath = Annotated[UUID, Path(description="Идентификатор санкции")]


def _stub() -> NotImplementedYetError:
    return NotImplementedYetError("ручка появится в потоке F3 (5c)")


def build_admin_router(services: AdminServices) -> APIRouter:
    """Ручки админки social."""
    router = APIRouter(prefix="/admin/v1", tags=["social-admin"])
    register_case_routes(router, services)
    register_config_routes(router, services)

    @router.get(
        "/users/{user_id}/moderation",
        response_model=UserModeration,
        summary="Игрок для модератора",
        description="Счётчики порядочности, история санкций, кейсы. Автосанкций нет.",
        responses=problem_responses(401, 403, 404, 501),
    )
    async def user_moderation(user_id: UserIdPath, moderator: Moderator) -> UserModeration:
        raise _stub()

    @router.post(
        "/users/{user_id}/sanctions",
        response_model=SanctionView,
        status_code=201,
        summary="Выдать санкцию",
        description=(
            "`ranked | chat | online | account`, со сроком или бессрочно. Публикует "
            "`UserSanctionsChanged` (полное активное состояние, `version`)."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501),
    )
    async def grant(
        user_id: UserIdPath, body: SanctionGrant, moderator: Sanctioner, key: IdempotencyKey
    ) -> SanctionView:
        raise _stub()

    @router.post(
        "/sanctions/{sanction_id}/revoke",
        response_model=SanctionView,
        summary="Снять санкцию",
        description="Ручное снятие; история сохраняется. Повтор - та же запись.",
        responses=problem_responses(401, 403, 404, 501),
    )
    async def revoke(
        sanction_id: SanctionIdPath,
        body: SanctionRevoke,
        moderator: Sanctioner,
        key: IdempotencyKey,
    ) -> SanctionView:
        raise _stub()

    return router
