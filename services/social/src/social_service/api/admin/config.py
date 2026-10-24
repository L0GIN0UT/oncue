"""Версии конфига social: /admin/v1/config/* (5c, поток F4; ADR 0053).

Права: `social.config.manage` (чтение, черновик), `social.config.publish` (публикация, откат).
Мутации: обязательные `Idempotency-Key` и `reason` (аудит в той же транзакции); публикация
привязана к `body_sha256` черновика. После публикации кэш конфига в процессе сбрасывается,
остальные реплики подхватывают версию по TTL кэша. Ответы - `no-store`.
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки права.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response

from gv_auth import Principal, require_perm
from gv_service import IdempotencyKey, problem_responses
from social_service.api.admin.services import AdminServices
from social_service.api.schemas import (
    ConfigVersionBody,
    ConfigVersionCard,
    ConfigVersionList,
    PublishConfig,
    RollbackConfig,
    SaveConfigDraft,
)
from social_service.application.config_admin import card

NO_STORE = "no-store"
ConfigManager = Annotated[Principal, Depends(require_perm("admin.access", "social.config.manage"))]
ConfigPublisher = Annotated[
    Principal, Depends(require_perm("admin.access", "social.config.publish"))
]
VersionPath = Annotated[int, Path(ge=1, description="Номер версии конфига")]


def register_config_routes(router: APIRouter, services: AdminServices) -> None:
    """Добавить в `/admin/v1` ручки версий конфига."""

    @router.get(
        "/config/versions",
        response_model=ConfigVersionList,
        summary="Версии конфига social",
        description="Черновик, действующая и архивные версии, новые первыми.",
        responses=problem_responses(401, 403),
    )
    async def config_versions(manager: ConfigManager, response: Response) -> ConfigVersionList:
        rows = await services.config_queries().versions()
        response.headers["Cache-Control"] = NO_STORE
        return ConfigVersionList(items=[ConfigVersionCard.model_validate(card(r)) for r in rows])

    @router.get(
        "/config/versions/{version}",
        response_model=ConfigVersionBody,
        summary="Версия конфига",
        description="Тело версии. Ошибки: 404 `social.config_version_not_found`.",
        responses=problem_responses(401, 403, 404, 422),
    )
    async def config_version(
        version: VersionPath, manager: ConfigManager, response: Response
    ) -> ConfigVersionBody:
        row = await services.config_queries().version(version)
        response.headers["Cache-Control"] = NO_STORE
        return ConfigVersionBody(card=ConfigVersionCard.model_validate(card(row)), config=row.body)

    @router.put(
        "/config/draft",
        response_model=ConfigVersionBody,
        summary="Сохранить черновик конфига",
        description=(
            "Заменяет черновик (он один); конфиг проверяется целиком по "
            "`contracts/social/social_config.v1.json` и границам схемы. Ошибки: 422 "
            "`social.config_invalid` (`details.errors` - пути и причины)."
        ),
        responses=problem_responses(401, 403, 409, 422),
    )
    async def save_config_draft(
        body: SaveConfigDraft, manager: ConfigManager, key: IdempotencyKey, response: Response
    ) -> ConfigVersionBody:
        data = await services.config_commands().save_draft(
            body.config, actor_id=manager.user_id, reason=body.reason, key=key
        )
        response.headers["Cache-Control"] = NO_STORE
        return ConfigVersionBody.model_validate(data)

    @router.post(
        "/config/draft/publish",
        response_model=ConfigVersionBody,
        summary="Опубликовать черновик",
        description=(
            "Новая версия действует без деплоя (кэш сервиса короткий), прежняя уходит в архив. "
            "Права `social.config.publish`. Ошибки: 404 `social.config_version_not_found`, "
            "409 `social.config_not_draft`, 409 `social.config_draft_changed` (тело изменилось)."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def publish_config(
        body: PublishConfig, publisher: ConfigPublisher, key: IdempotencyKey, response: Response
    ) -> ConfigVersionBody:
        data = await services.config_commands().publish(
            body.version, body.body_sha256, actor_id=publisher.user_id, reason=body.reason, key=key
        )
        services.config_changed()
        response.headers["Cache-Control"] = NO_STORE
        return ConfigVersionBody.model_validate(data)

    @router.post(
        "/config/versions/{version}/rollback",
        response_model=ConfigVersionBody,
        status_code=201,
        summary="Откатить к версии",
        description=(
            "Копия старой версии становится черновиком (заменяет текущий); публикуется отдельно. "
            "Ошибки: 404 `social.config_version_not_found`, 409 `social.config_never_published`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def rollback_config(
        version: VersionPath,
        body: RollbackConfig,
        publisher: ConfigPublisher,
        key: IdempotencyKey,
        response: Response,
    ) -> ConfigVersionBody:
        data = await services.config_commands().rollback(
            version, actor_id=publisher.user_id, reason=body.reason, key=key
        )
        response.headers["Cache-Control"] = NO_STORE
        return ConfigVersionBody.model_validate(data)
