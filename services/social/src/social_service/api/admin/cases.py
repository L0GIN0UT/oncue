"""Очередь модерации: /admin/v1/cases (5c, поток F4; ADR 0051, 0052).

Права `admin.access` + `social.moderate`. Действия над кейсом: обязательные `Idempotency-Key`,
`version` (оптимистичная блокировка) и причина (в аудит). Эскалированный кейс решает только
старший - обладатель `social.sanctions.account` (в identity он у admin, у moderator нет).
Текст жалоб возвращается только здесь и не попадает в события.
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки права.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response

from gv_auth import Principal, require_perm
from gv_core.errors import ForbiddenError
from gv_service import IdempotencyKey, problem_responses
from social_service.api.admin.services import AdminServices
from social_service.api.schemas import (
    CaseClaim,
    CaseDetail,
    CaseEscalate,
    CasePage,
    CaseResolve,
    CaseStatus,
    CaseSummary,
    Cursor,
    TargetKind,
)
from social_service.application.cases import SanctionSpec, Staff

NO_STORE = "no-store"
Moderator = Annotated[Principal, Depends(require_perm("admin.access", "social.moderate"))]
CaseIdPath = Annotated[UUID, Path(description="Идентификатор кейса")]


def _staff(principal: Principal) -> Staff:
    return Staff(user_id=principal.user_id, perms=principal.perms)


def _require_sanction_perms(principal: Principal, kind: str | None) -> None:
    """Права на санкцию проверяются здесь, не полагаясь на реализацию гранта (ADR 0052)."""
    if not principal.has_all("social.sanctions.manage"):
        raise ForbiddenError("недостаточно прав", code="auth.forbidden")
    if kind == "account" and not principal.has_all("social.sanctions.account"):
        raise ForbiddenError(
            "бан аккаунта требует отдельного права", code="social.account_sanction_forbidden"
        )


def register_case_routes(router: APIRouter, services: AdminServices) -> None:
    """Добавить в `/admin/v1` ручки очереди модерации."""

    @router.get(
        "/cases",
        response_model=CasePage,
        summary="Очередь модерации",
        description="Старые первыми; фильтры по статусу, объекту и «мои» (`mine`).",
        responses=problem_responses(401, 403, 422),
    )
    async def cases(
        moderator: Moderator,
        response: Response,
        *,
        status: Annotated[CaseStatus | None, Query()] = None,
        target_kind: Annotated[TargetKind | None, Query()] = None,
        mine: Annotated[bool, Query(description="Только взятые мной")] = False,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> CasePage:
        data = await services.cases.queue(
            _staff(moderator),
            status=status,
            target_kind=target_kind,
            mine=mine,
            cursor=cursor,
            limit=limit,
        )
        response.headers["Cache-Control"] = NO_STORE
        return CasePage.model_validate(data)

    @router.get(
        "/cases/{case_id}",
        response_model=CaseDetail,
        summary="Карточка кейса",
        description="Жалобы с текстом (только модератору) и фрагмент содержимого объекта.",
        responses=problem_responses(401, 403, 404),
    )
    async def case(case_id: CaseIdPath, moderator: Moderator, response: Response) -> CaseDetail:
        data = await services.cases.detail(case_id)
        response.headers["Cache-Control"] = NO_STORE
        return CaseDetail.model_validate(data)

    @router.post(
        "/cases/{case_id}/claim",
        response_model=CaseSummary,
        summary="Взять в работу",
        description=(
            "`open` -> `in_work`. Уже взятый другим: 409 `social.case_already_claimed`; "
            "устаревшая `version`: 409 `social.case_version_conflict`."
        ),
        responses=problem_responses(401, 403, 404, 409),
    )
    async def claim(
        case_id: CaseIdPath,
        body: CaseClaim,
        moderator: Moderator,
        key: IdempotencyKey,
        response: Response,
    ) -> CaseSummary:
        data = await services.cases.claim(_staff(moderator), case_id, body.version, key)
        response.headers["Cache-Control"] = NO_STORE
        return CaseSummary.model_validate(data)

    @router.post(
        "/cases/{case_id}/escalate",
        response_model=CaseSummary,
        summary="Эскалировать",
        description="`in_work` -> `escalated`; решить эскалированный может только admin.",
        responses=problem_responses(401, 403, 404, 409),
    )
    async def escalate(
        case_id: CaseIdPath,
        body: CaseEscalate,
        moderator: Moderator,
        key: IdempotencyKey,
        response: Response,
    ) -> CaseSummary:
        data = await services.cases.escalate(
            _staff(moderator), case_id, body.version, body.reason, key
        )
        response.headers["Cache-Control"] = NO_STORE
        return CaseSummary.model_validate(data)

    @router.post(
        "/cases/{case_id}/resolve",
        response_model=CaseDetail,
        summary="Решить кейс",
        description=(
            "`dismissed` (отклонить), `content_removed` (удалить контент), `sanctioned` "
            "(санкция в теле). Одна транзакция: кейс, контент, санкция, outbox "
            "`ContentModerated`, `ReportResolved` (по заявителю), `UserSanctionsChanged`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501),
    )
    async def resolve(
        case_id: CaseIdPath,
        body: CaseResolve,
        moderator: Moderator,
        key: IdempotencyKey,
        response: Response,
    ) -> CaseDetail:
        if body.decision == "sanctioned":
            _require_sanction_perms(moderator, body.sanction.kind if body.sanction else None)
        sanction = (
            SanctionSpec(body.sanction.kind, body.sanction.duration_s, body.sanction.reason)
            if body.sanction is not None
            else None
        )
        data = await services.cases.resolve(
            _staff(moderator),
            case_id,
            version=body.version,
            decision=body.decision,
            sanction=sanction,
            reason=body.reason,
            key=key,
        )
        response.headers["Cache-Control"] = NO_STORE
        return CaseDetail.model_validate(data)
