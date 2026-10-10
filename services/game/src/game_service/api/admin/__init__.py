"""Admin API (/admin/v1): конфиги режимов и наборы дня (план этапа 2, §2.6).

Шаг 0a: схемы и заглушки (501 ``common.not_implemented``); реализация — поток B.
Доступ — ``admin.access`` + конкретное право; мутации — ``Idempotency-Key`` и ``reason``.
"""

# ruff: noqa: ARG001 — параметры заглушек объявлены ради контракта OpenAPI; снять в потоке B.

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query

from game_service.api.admin.schemas import (
    ArchiveRequest,
    ConfigStatus,
    DailySet,
    DraftOut,
    ModeConfigDetail,
    ModeConfigSummary,
    ModeConfigVersion,
    ProjectionItem,
    PublishRequest,
    RegenerateRequest,
    ReplaceItemRequest,
    RollbackRequest,
    SaveDraftRequest,
    ValidateRequest,
    ValidationResult,
)
from game_service.api.common import not_implemented
from game_service.api.public.schemas import Domain
from gv_auth import Principal, require_perm
from gv_service import IdempotencyKey, Infrastructure, problem_responses

ADMIN_ACCESS = "admin.access"
ConfigViewer = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "config.modes.view"))]
ConfigEditor = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "config.modes.edit"))]
ConfigPublisher = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "config.modes.publish"))]
DailyViewer = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "game.daily.view"))]
DailyCurator = Annotated[Principal, Depends(require_perm(ADMIN_ACCESS, "game.daily.curate"))]

ModeKeyPath = Annotated[
    str,
    Path(pattern=r"^(music|film)\.[a-z0-9_]+\.[a-z0-9_]+$", max_length=100),
]
_READ = problem_responses(401, 403, 422, 501)
_READ_ONE = problem_responses(401, 403, 404, 422, 501)
_MUTATE = problem_responses(401, 403, 404, 409, 422, 501)


def build_admin_router(infra: Infrastructure) -> APIRouter:
    """Админские ручки game."""
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    # ------------------------------------------------------------------ конфиги режимов
    @router.get(
        "/mode-configs",
        response_model=list[ModeConfigSummary],
        summary="Режимы (конфиги)",
        responses=_READ,
    )
    async def list_mode_configs(
        principal: ConfigViewer,
        domain: Annotated[Domain | None, Query()] = None,
        status: Annotated[ConfigStatus | None, Query()] = None,
    ) -> list[ModeConfigSummary]:
        not_implemented()

    # Объявлена раньше /mode-configs/{mode_key}: иначе «schema» попала бы в {mode_key}.
    # Параметр пути — mode_key, а не key: у зависимости Idempotency-Key свой параметр key,
    # и FastAPI принял бы его за параметр пути.
    @router.get(
        "/mode-configs/schema",
        response_model=dict[str, Any],
        summary="Сводная JSON Schema конфига (подсказки редактора)",
        responses=_READ,
    )
    async def mode_config_schema(principal: ConfigViewer) -> dict[str, Any]:
        not_implemented()

    @router.post(
        "/mode-configs/validate",
        response_model=ValidationResult,
        summary="Проверить тело конфига",
        description="Без сохранения; ошибки с JSON-путями и размер пула по проекции.",
        responses=problem_responses(401, 403, 422, 501),
    )
    async def validate_mode_config(
        body: ValidateRequest, principal: ConfigViewer
    ) -> ValidationResult:
        not_implemented()

    @router.get(
        "/mode-configs/{mode_key}",
        response_model=ModeConfigDetail,
        summary="Режим: версии и черновик",
        responses=_READ_ONE,
    )
    async def get_mode_config(mode_key: ModeKeyPath, principal: ConfigViewer) -> ModeConfigDetail:
        not_implemented()

    @router.get(
        "/mode-configs/{mode_key}/versions/{version}",
        response_model=ModeConfigVersion,
        summary="Версия конфига",
        responses=_READ_ONE,
    )
    async def get_mode_config_version(
        mode_key: ModeKeyPath, version: Annotated[int, Path(ge=1)], principal: ConfigViewer
    ) -> ModeConfigVersion:
        not_implemented()

    @router.put(
        "/mode-configs/{mode_key}/draft",
        response_model=DraftOut,
        summary="Сохранить черновик",
        description=(
            "Сохраняет и невалидный черновик; итог проверки — в `validation`. "
            "409 `game.draft_conflict` — `base_revision` устарела."
        ),
        responses=_MUTATE,
    )
    async def save_draft(
        mode_key: ModeKeyPath,
        body: SaveDraftRequest,
        principal: ConfigEditor,
        idempotency_key: IdempotencyKey,
    ) -> DraftOut:
        not_implemented()

    @router.post(
        "/mode-configs/{mode_key}/publish",
        response_model=ModeConfigVersion,
        summary="Опубликовать черновик",
        description=(
            "Только валидный черновик ревизии `draft_revision` → новая неизменяемая версия, "
            "событие `game.ModeConfigPublished`. 422 `game.config_invalid`, "
            "409 `game.draft_conflict`."
        ),
        responses=_MUTATE,
    )
    async def publish_mode_config(
        mode_key: ModeKeyPath,
        body: PublishRequest,
        principal: ConfigPublisher,
        idempotency_key: IdempotencyKey,
    ) -> ModeConfigVersion:
        not_implemented()

    @router.post(
        "/mode-configs/{mode_key}/rollback",
        response_model=ModeConfigVersion,
        summary="Откатить к версии",
        responses=_MUTATE,
    )
    async def rollback_mode_config(
        mode_key: ModeKeyPath,
        body: RollbackRequest,
        principal: ConfigPublisher,
        idempotency_key: IdempotencyKey,
    ) -> ModeConfigVersion:
        not_implemented()

    @router.post(
        "/mode-configs/{mode_key}/archive",
        response_model=ModeConfigSummary,
        summary="Архивировать режим",
        responses=_MUTATE,
    )
    async def archive_mode_config(
        mode_key: ModeKeyPath,
        body: ArchiveRequest,
        principal: ConfigPublisher,
        idempotency_key: IdempotencyKey,
    ) -> ModeConfigSummary:
        not_implemented()

    # ------------------------------------------------------------------ наборы дня
    @router.get(
        "/daily-sets",
        response_model=list[DailySet],
        summary="Наборы дня за период",
        responses=_READ,
    )
    async def list_daily_sets(
        principal: DailyViewer,
        mode_key: Annotated[str, Query(max_length=100)],
        date_from: Annotated[date, Query(alias="from")],
        date_to: Annotated[date, Query(alias="to")],
    ) -> list[DailySet]:
        not_implemented()

    @router.put(
        "/daily-sets/{mode_key}/{day}/items/{index}",
        response_model=DailySet,
        summary="Заменить единицу набора",
        description=(
            "409 `game.daily_locked` (день начался), `game.item_not_playable`, "
            "`game.item_duplicate`."
        ),
        responses=_MUTATE,
    )
    async def replace_daily_item(
        *,
        mode_key: ModeKeyPath,
        day: date,
        index: Annotated[int, Path(ge=0, le=49)],
        body: ReplaceItemRequest,
        principal: DailyCurator,
        idempotency_key: IdempotencyKey,
    ) -> DailySet:
        not_implemented()

    @router.post(
        "/daily-sets/{mode_key}/{day}/regenerate",
        response_model=DailySet,
        summary="Перегенерировать набор",
        responses=_MUTATE,
    )
    async def regenerate_daily_set(
        mode_key: ModeKeyPath,
        day: date,
        body: RegenerateRequest,
        principal: DailyCurator,
        idempotency_key: IdempotencyKey,
    ) -> DailySet:
        not_implemented()

    @router.get(
        "/projection/items",
        response_model=list[ProjectionItem],
        summary="Поиск кандидатов в проекции game",
        responses=_READ,
    )
    async def search_projection(
        principal: DailyViewer,
        q: Annotated[str | None, Query(max_length=64)] = None,
        domain: Annotated[Domain | None, Query()] = None,
        playable_for_mode: Annotated[str | None, Query(max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> list[ProjectionItem]:
        not_implemented()

    return router
