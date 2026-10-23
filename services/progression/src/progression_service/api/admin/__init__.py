"""Admin API (/admin/v1): конфиги, ручная выдача Версов, сторно, задания и бейджи.

Контракт этапа 5a (``docs/plans/stage-5.md`` §2.4, ADR 0042–0044), реализация - поток P3.

Права: ``admin.access`` + одно из ``progression.config.manage`` (черновик, чтение),
``progression.config.publish`` (публикация, откат), ``progression.ledger.grant`` (выдача),
``progression.ledger.reverse`` (сторно), ``progression.ledger.read`` (чтение журнала).
Мутации - ``Idempotency-Key`` и ``reason`` (в аудит); успешный ответ без кэша.
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки права.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status

from gv_auth import Principal, require_perm
from gv_service import IdempotencyKey, problem_responses
from progression_service.api.admin.schemas import (
    AdminBadgeList,
    AdminLedgerPage,
    AdminQuestList,
    ConfigVersionBody,
    ConfigVersionList,
    Cursor,
    GrantRequest,
    LedgerOperation,
    PublishRequest,
    ReverseRequest,
    RollbackRequest,
    SaveDraftRequest,
)
from progression_service.api.admin.shop import register_shop_admin_routes
from progression_service.application.admin import AdminServices
from progression_service.application.admin.common import AdminContext

ADMIN_ACCESS = "admin.access"
Manager = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "progression.config.manage"))]
Publisher = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "progression.config.publish"))]
Granter = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "progression.ledger.grant"))]
Reverser = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "progression.ledger.reverse"))]
LedgerReader = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "progression.ledger.read"))]
UserIdPath = Annotated[UUID, Path(description="Идентификатор пользователя")]
VersionPath = Annotated[int, Path(ge=1, description="Версия конфига")]
OpIdPath = Annotated[str, Path(min_length=1, max_length=200, description="`op_id` проводки")]
NO_STORE = "no-store"


def _ctx(principal: Principal, reason: str, key: str) -> AdminContext:
    return AdminContext(actor_id=principal.user_id, reason=reason, idempotency_key=key)


def build_admin_router(services: AdminServices) -> APIRouter:
    """Ручки админки progression.

    ``Cache-Control: no-store`` ставится после успешного выполнения: ошибка (Problem) уходит
    своим ответом, заголовок на неё не переносится.
    """
    router = APIRouter(prefix="/admin/v1", tags=["progression-admin"])

    @router.get(
        "/configs",
        response_model=ConfigVersionList,
        summary="Версии конфига",
        description="Черновик, действующая и архивные версии, новые первыми.",
        responses=problem_responses(401, 403),
    )
    async def versions(response: Response, principal: Manager) -> ConfigVersionList:
        result = ConfigVersionList.model_validate(await services.config_queries.versions())
        response.headers["Cache-Control"] = NO_STORE
        return result

    @router.get(
        "/configs/{version}",
        response_model=ConfigVersionBody,
        summary="Версия конфига",
        description="Тело версии. Ошибки: 404 `progression.config_version_not_found`.",
        responses=problem_responses(401, 403, 404, 422),
    )
    async def version(
        version: VersionPath, response: Response, principal: Manager
    ) -> ConfigVersionBody:
        result = ConfigVersionBody.model_validate(await services.config_queries.version(version))
        response.headers["Cache-Control"] = NO_STORE
        return result

    @router.put(
        "/configs/draft",
        response_model=ConfigVersionBody,
        summary="Сохранить черновик",
        description=(
            "Заменяет черновик (он один); конфиг проверяется целиком. Ошибки: 422 "
            "`progression.config_invalid` (`details.errors` - пути и причины)."
        ),
        responses=problem_responses(401, 403, 409, 422),
    )
    async def save_draft(
        body: SaveDraftRequest, response: Response, principal: Manager, key: IdempotencyKey
    ) -> ConfigVersionBody:
        result = await services.configs.save_draft(
            body.config, ctx=_ctx(principal, body.reason, key)
        )
        response.headers["Cache-Control"] = NO_STORE
        return ConfigVersionBody.model_validate(result)

    @router.post(
        "/configs/draft/publish",
        response_model=ConfigVersionBody,
        summary="Опубликовать черновик",
        description=(
            "Новая версия действует для новых проводок, прошлое не пересчитывается; прежняя "
            "действующая уходит в архив. Ошибки: 404 `progression.config_version_not_found`, "
            "409 `progression.config_not_draft`, 409 `progression.draft_changed` (тело "
            "черновика изменилось)."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def publish(
        body: PublishRequest, response: Response, principal: Publisher, key: IdempotencyKey
    ) -> ConfigVersionBody:
        result = await services.configs.publish(
            body.version, body.body_sha256, ctx=_ctx(principal, body.reason, key)
        )
        response.headers["Cache-Control"] = NO_STORE
        return ConfigVersionBody.model_validate(result)

    @router.post(
        "/configs/{version}/rollback",
        response_model=ConfigVersionBody,
        status_code=status.HTTP_201_CREATED,
        summary="Откатить к версии",
        description=(
            "Копия старой версии становится черновиком (заменяет текущий). Ошибки: 404 "
            "`progression.config_version_not_found`, 409 `progression.config_never_published`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def rollback(
        version: VersionPath,
        body: RollbackRequest,
        response: Response,
        principal: Publisher,
        key: IdempotencyKey,
    ) -> ConfigVersionBody:
        result = await services.configs.rollback(version, ctx=_ctx(principal, body.reason, key))
        response.headers["Cache-Control"] = NO_STORE
        return ConfigVersionBody.model_validate(result)

    @router.get(
        "/users/{user_id}/ledger",
        response_model=AdminLedgerPage,
        summary="Журнал игрока",
        description=(
            "Проводки Версов, новые первыми, курсорная пагинация. Ошибки: 404 "
            "`progression.user_not_found`."
        ),
        responses=problem_responses(401, 403, 404, 422),
    )
    async def ledger(
        user_id: UserIdPath,
        response: Response,
        principal: LedgerReader,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> AdminLedgerPage:
        result = AdminLedgerPage.model_validate(
            await services.ledger_queries.page(user_id, cursor=cursor, limit=limit)
        )
        response.headers["Cache-Control"] = NO_STORE
        return result

    @router.post(
        "/users/{user_id}/ledger/grant",
        response_model=LedgerOperation,
        status_code=status.HTTP_201_CREATED,
        summary="Выдать Версы вручную",
        description=(
            "Проводка `admin:{Idempotency-Key}`; предел - `admin.grant_max_amount` и "
            "`admin.grant_max_per_user_day` конфига. Ошибки: 404 `progression.user_not_found`, "
            "422 `progression.grant_limit_exceeded`, 422 `progression.reason_too_short`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 503),
    )
    async def grant(
        user_id: UserIdPath,
        body: GrantRequest,
        response: Response,
        principal: Granter,
        key: IdempotencyKey,
    ) -> LedgerOperation:
        result = await services.ledger.grant(
            user_id, amount=body.amount, ctx=_ctx(principal, body.reason, key)
        )
        response.headers["Cache-Control"] = NO_STORE
        return LedgerOperation.model_validate(result)

    @router.post(
        "/ledger/{op_id}/reverse",
        response_model=LedgerOperation,
        status_code=status.HTTP_201_CREATED,
        summary="Сторнировать проводку",
        description=(
            "Встречная проводка со ссылкой `reversal_of`; журнал не правится; только журнал "
            "Версов, только кредиты. Ошибки: 404 `progression.ledger_entry_not_found`, 409 "
            "`progression.already_reversed`, 409 `progression.not_reversible` (дебет, сторно, "
            "проводка опыта), "
            "409 `progression.insufficient_balance` (баланс ушёл бы ниже нуля)."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 503),
    )
    async def reverse(
        op_id: OpIdPath,
        body: ReverseRequest,
        response: Response,
        principal: Reverser,
        key: IdempotencyKey,
    ) -> LedgerOperation:
        result = await services.ledger.reverse(op_id, ctx=_ctx(principal, body.reason, key))
        response.headers["Cache-Control"] = NO_STORE
        return LedgerOperation.model_validate(result)

    @router.get(
        "/quests",
        response_model=AdminQuestList,
        summary="Задания",
        description="Задания действующего конфига с числом выполнений. Правка - через черновик.",
        responses=problem_responses(401, 403, 503),
    )
    async def quests(response: Response, principal: Manager) -> AdminQuestList:
        result = AdminQuestList.model_validate(await services.config_queries.quests())
        response.headers["Cache-Control"] = NO_STORE
        return result

    @router.get(
        "/badges",
        response_model=AdminBadgeList,
        summary="Бейджи",
        description="Бейджи действующего конфига с числом выданных. Правка - через черновик.",
        responses=problem_responses(401, 403, 503),
    )
    async def badges(response: Response, principal: Manager) -> AdminBadgeList:
        result = AdminBadgeList.model_validate(await services.config_queries.badges())
        response.headers["Cache-Control"] = NO_STORE
        return result

    register_shop_admin_routes(router)
    return router
