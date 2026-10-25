"""Админ-ручки санкций (5c, поток F3; ADR 0052): выдача, снятие, карточка игрока.

* права: `admin.access` + `social.sanctions.manage` (выдача и снятие), карточка игрока -
  `social.moderate`; `account` дополнительно требует `social.sanctions.account`
  (проверка в application-слое: 403 `social.account_sanction_forbidden`);
* модератор не выдаёт и не снимает санкции себе (403 `social.self_sanction_forbidden`);
* выдать можно, только если права цели ⊆ прав актёра, иначе 403 `social.sanction_target_outranks`
  (ADR 0058);
* аудит - `actor_id` и `revoked_by` записи и `changed_by` события `UserSanctionsChanged`;
* ответы - `Cache-Control: no-store`.
"""

# Аргументы ручек - зависимости FastAPI (право, ключ идемпотентности): часть нужна только ради них.
# ruff: noqa: ARG001, PLR0917
from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, Response

from gv_auth import Principal, require_perm
from gv_service import IdempotencyKey, problem_responses
from social_service.api.schemas import (
    BehaviourCounters,
    CaseSummary,
    SanctionGrant,
    SanctionRevoke,
    SanctionView,
    UserModeration,
)
from social_service.application.ports.sanctions import GrantRequest, SanctionRecord
from social_service.application.sanctions import SanctionService
from social_service.domain.sanctions import is_active

Sanctioner = Annotated[Principal, Depends(require_perm("admin.access", "social.sanctions.manage"))]
Moderator = Annotated[Principal, Depends(require_perm("admin.access", "social.moderate"))]
UserIdPath = Annotated[UUID, Path(description="Идентификатор игрока")]
SanctionIdPath = Annotated[UUID, Path(description="Идентификатор санкции")]
NO_STORE = "no-store"
_COUNTER_FIELDS = tuple(BehaviourCounters.model_fields)


def sanction_view(record: SanctionRecord, now: datetime) -> SanctionView:
    """Запись истории в схему ответа."""
    return SanctionView(
        id=record.id,
        user_id=record.user_id,
        kind=record.kind,
        started_at=record.started_at,
        expires_at=record.expires_at,
        reason=record.reason,
        actor_id=record.actor_id,
        revoked_at=record.revoked_at,
        revoked_by=record.revoked_by,
        revoke_reason=record.revoke_reason,
        active=is_active(record.expires_at, record.revoked_at, now),
    )


def _now(request: Request) -> datetime:
    return request.app.state.infra.clock.now()  # type: ignore[no-any-return]  # app.state нетипизирован


def build_sanctions_router(service: SanctionService) -> APIRouter:
    """Ручки санкций; подключается в `/admin/v1` одной строкой."""
    router = APIRouter()  # префикс и тег задаёт родительский роутер /admin/v1

    @router.get(
        "/users/{user_id}/moderation",
        response_model=UserModeration,
        summary="Игрок для модератора",
        description="Счётчики порядочности, история санкций, кейсы. Автосанкций нет.",
        responses=problem_responses(401, 403, 404),
    )
    async def user_moderation(
        user_id: UserIdPath, moderator: Moderator, response: Response
    ) -> UserModeration:
        card, history, now = await service.moderation_card(user_id)
        response.headers["Cache-Control"] = NO_STORE
        return UserModeration(
            user_id=user_id,
            nickname=card.nickname,
            counters=BehaviourCounters(**{f: card.counters.get(f, 0) for f in _COUNTER_FIELDS}),
            sanctions=[sanction_view(r, now) for r in history],
            cases=[CaseSummary.model_validate(c, from_attributes=True) for c in card.cases],
        )

    @router.post(
        "/users/{user_id}/sanctions",
        response_model=SanctionView,
        status_code=201,
        summary="Выдать санкцию",
        description=(
            "`ranked | chat | online | account`, со сроком или бессрочно. Публикует "
            "`UserSanctionsChanged` (полное активное состояние, `version`). Повтор того же вида "
            "заменяет прежнюю санкцию (она закрывается как `replaced`). Ошибки: 403 "
            "`social.self_sanction_forbidden`, 403 `social.account_sanction_forbidden`, "
            "403 `social.sanction_target_outranks` (права игрока шире ваших), 422 "
            "`social.sanction_duration_invalid`, 404 `social.player_not_found`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def grant(
        user_id: UserIdPath,
        body: SanctionGrant,
        moderator: Sanctioner,
        key: IdempotencyKey,
        request: Request,
        response: Response,
    ) -> SanctionView:
        record, _ = await service.grant_idempotent(
            GrantRequest(
                actor_id=moderator.user_id,
                actor_perms=moderator.perms,
                user_id=user_id,
                kind=body.kind,
                duration_s=body.duration_s,
                reason=body.reason,
            ),
            key,
        )
        response.headers["Cache-Control"] = NO_STORE
        return sanction_view(record, _now(request))

    @router.post(
        "/sanctions/{sanction_id}/revoke",
        response_model=SanctionView,
        summary="Снять санкцию",
        description="Ручное снятие; история сохраняется. Повтор - та же запись.",
        responses=problem_responses(401, 403, 404),
    )
    async def revoke(
        sanction_id: SanctionIdPath,
        body: SanctionRevoke,
        moderator: Sanctioner,
        key: IdempotencyKey,
        request: Request,
        response: Response,
    ) -> SanctionView:
        record = await service.revoke(moderator.user_id, sanction_id, body.reason, moderator.perms)
        response.headers["Cache-Control"] = NO_STORE
        return sanction_view(record, _now(request))

    return router
