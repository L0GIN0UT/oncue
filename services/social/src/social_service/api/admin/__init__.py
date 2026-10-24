"""Admin API (/admin/v1): очередь модерации и санкции (5c, поток F4; ADR 0051, 0052).

Шаг 0a: ручки объявлены, реализация - потоки F3/F4; до тех пор отвечают 501.

* конфиг (ADR 0053) - `social.config.manage` (черновик), `social.config.publish` (публикация);
* очередь - `admin.access` + `social.moderate`; санкции `ranked|chat|online` -
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

from fastapi import APIRouter, Depends, Path, Query

from gv_auth import Principal, require_perm
from gv_core.errors import NotImplementedYetError
from gv_service import IdempotencyKey, problem_responses
from social_service.api.schemas import (
    CaseClaim,
    CaseDetail,
    CaseEscalate,
    CasePage,
    CaseResolve,
    CaseStatus,
    CaseSummary,
    ConfigVersionBody,
    ConfigVersionList,
    Cursor,
    PublishConfig,
    RollbackConfig,
    SanctionGrant,
    SanctionRevoke,
    SanctionView,
    SaveConfigDraft,
    TargetKind,
    UserModeration,
)

Moderator = Annotated[Principal, Depends(require_perm("admin.access", "social.moderate"))]
Sanctioner = Annotated[Principal, Depends(require_perm("admin.access", "social.sanctions.manage"))]
ConfigManager = Annotated[Principal, Depends(require_perm("admin.access", "social.config.manage"))]
ConfigPublisher = Annotated[
    Principal, Depends(require_perm("admin.access", "social.config.publish"))
]
VersionPath = Annotated[int, Path(ge=1, description="Номер версии конфига")]
CaseIdPath = Annotated[UUID, Path(description="Идентификатор кейса")]
UserIdPath = Annotated[UUID, Path(description="Идентификатор игрока")]
SanctionIdPath = Annotated[UUID, Path(description="Идентификатор санкции")]


def _stub() -> NotImplementedYetError:
    return NotImplementedYetError("ручка появится в потоках F3/F4 (5c)")


def build_admin_router() -> APIRouter:
    """Ручки админки social."""
    router = APIRouter(prefix="/admin/v1", tags=["social-admin"])

    @router.get(
        "/cases",
        response_model=CasePage,
        summary="Очередь модерации",
        description="Старые первыми; фильтры по статусу, объекту и «мои» (`assignee=me`).",
        responses=problem_responses(401, 403, 422, 501),
    )
    async def cases(
        moderator: Moderator,
        *,
        status: Annotated[CaseStatus | None, Query()] = None,
        target_kind: Annotated[TargetKind | None, Query()] = None,
        mine: Annotated[bool, Query(description="Только взятые мной")] = False,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> CasePage:
        raise _stub()

    @router.get(
        "/cases/{case_id}",
        response_model=CaseDetail,
        summary="Карточка кейса",
        responses=problem_responses(401, 403, 404, 501),
    )
    async def case(case_id: CaseIdPath, moderator: Moderator) -> CaseDetail:
        raise _stub()

    @router.post(
        "/cases/{case_id}/claim",
        response_model=CaseSummary,
        summary="Взять в работу",
        description="`open` -> `in_work`. Уже взятый другим: 409 `social.case_already_claimed`.",
        responses=problem_responses(401, 403, 404, 409, 501),
    )
    async def claim(
        case_id: CaseIdPath, body: CaseClaim, moderator: Moderator, key: IdempotencyKey
    ) -> CaseSummary:
        raise _stub()

    @router.post(
        "/cases/{case_id}/escalate",
        response_model=CaseSummary,
        summary="Эскалировать",
        description="`in_work` -> `escalated`; решить эскалированный может только admin.",
        responses=problem_responses(401, 403, 404, 409, 501),
    )
    async def escalate(
        case_id: CaseIdPath, body: CaseEscalate, moderator: Moderator, key: IdempotencyKey
    ) -> CaseSummary:
        raise _stub()

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
        case_id: CaseIdPath, body: CaseResolve, moderator: Moderator, key: IdempotencyKey
    ) -> CaseDetail:
        raise _stub()

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

    @router.get(
        "/config/versions",
        response_model=ConfigVersionList,
        summary="Версии конфига social",
        description="Черновик, действующая и архивные версии, новые первыми.",
        responses=problem_responses(401, 403, 501),
    )
    async def config_versions(manager: ConfigManager) -> ConfigVersionList:
        raise _stub()

    @router.get(
        "/config/versions/{version}",
        response_model=ConfigVersionBody,
        summary="Версия конфига",
        description="Тело версии. Ошибки: 404 `social.config_version_not_found`.",
        responses=problem_responses(401, 403, 404, 422, 501),
    )
    async def config_version(version: VersionPath, manager: ConfigManager) -> ConfigVersionBody:
        raise _stub()

    @router.put(
        "/config/draft",
        response_model=ConfigVersionBody,
        summary="Сохранить черновик конфига",
        description=(
            "Заменяет черновик (он один); конфиг проверяется целиком по "
            "`contracts/social/social_config.v1.json` и границам схемы. Ошибки: 422 "
            "`social.config_invalid` (`details.errors` - пути и причины)."
        ),
        responses=problem_responses(401, 403, 409, 422, 501),
    )
    async def save_config_draft(
        body: SaveConfigDraft, manager: ConfigManager, key: IdempotencyKey
    ) -> ConfigVersionBody:
        raise _stub()

    @router.post(
        "/config/draft/publish",
        response_model=ConfigVersionBody,
        summary="Опубликовать черновик",
        description=(
            "Новая версия действует без деплоя (кэш сервиса короткий), прежняя уходит в архив. "
            "Права `social.config.publish`. Ошибки: 404 `social.config_version_not_found`, "
            "409 `social.config_not_draft`, 409 `social.draft_changed` (тело изменилось)."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501),
    )
    async def publish_config(
        body: PublishConfig, publisher: ConfigPublisher, key: IdempotencyKey
    ) -> ConfigVersionBody:
        raise _stub()

    @router.post(
        "/config/versions/{version}/rollback",
        response_model=ConfigVersionBody,
        status_code=201,
        summary="Откатить к версии",
        description=(
            "Копия старой версии становится черновиком (заменяет текущий); публикуется отдельно. "
            "Ошибки: 404 `social.config_version_not_found`, 409 `social.config_never_published`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501),
    )
    async def rollback_config(
        version: VersionPath, body: RollbackConfig, publisher: ConfigPublisher, key: IdempotencyKey
    ) -> ConfigVersionBody:
        raise _stub()

    return router
