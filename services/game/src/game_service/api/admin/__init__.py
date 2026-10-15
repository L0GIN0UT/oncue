"""Admin API (/admin/v1): конфиги режимов и наборы дня (план этапа 2, §2.6).

Доступ — ``admin.access`` + конкретное право; мутации — ``Idempotency-Key`` и ``reason``.
"""

# ruff: noqa: ARG001 — параметр principal нужен ради зависимости с проверкой права.

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query

from game_service.api.admin.schemas import (
    ArchiveRequest,
    AssetRequestCreate,
    AssetRequestOut,
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
from game_service.api.common import ApiServices, NoStoreRoute
from game_service.api.public.schemas import Domain
from game_service.application.commands.modes import AdminContext
from gv_auth import Principal, require_perm
from gv_core.errors import NotImplementedYetError
from gv_service import IdempotencyKey, problem_responses

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
_READ = problem_responses(401, 403, 422)
_READ_ONE = problem_responses(401, 403, 404, 422, 503)
_MUTATE = problem_responses(401, 403, 404, 409, 422, 503)


def _ctx(principal: Principal, reason: str, key: str) -> AdminContext:
    return AdminContext(actor_id=principal.user_id, reason=reason, idempotency_key=key)


def build_admin_router(c: ApiServices) -> APIRouter:
    """Админские ручки game."""
    router = APIRouter(prefix="/admin/v1", tags=["admin"], route_class=NoStoreRoute)

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
        rows = await c.mode_queries.list_modes(domain=domain, status=status)
        return [ModeConfigSummary.model_validate(r) for r in rows]

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
        return c.editor_schema()

    @router.post(
        "/mode-configs/validate",
        response_model=ValidationResult,
        summary="Проверить тело конфига",
        description="Без сохранения; ошибки с JSON-путями и размер пула по проекции.",
        responses=problem_responses(401, 403, 422),
    )
    async def validate_mode_config(
        body: ValidateRequest, principal: ConfigViewer
    ) -> ValidationResult:
        return ValidationResult.model_validate(await c.mode_queries.validate(body.body))

    @router.get(
        "/mode-configs/{mode_key}",
        response_model=ModeConfigDetail,
        summary="Режим: версии и черновик",
        responses=_READ_ONE,
    )
    async def get_mode_config(mode_key: ModeKeyPath, principal: ConfigViewer) -> ModeConfigDetail:
        return ModeConfigDetail.model_validate(await c.mode_queries.detail(mode_key))

    @router.get(
        "/mode-configs/{mode_key}/versions/{version}",
        response_model=ModeConfigVersion,
        summary="Версия конфига",
        responses=_READ_ONE,
    )
    async def get_mode_config_version(
        mode_key: ModeKeyPath, version: Annotated[int, Path(ge=1)], principal: ConfigViewer
    ) -> ModeConfigVersion:
        return ModeConfigVersion.model_validate(await c.mode_queries.version(mode_key, version))

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
        draft = await c.mode_commands.save_draft(
            mode_key,
            body.body,
            base_revision=body.base_revision,
            ctx=_ctx(principal, body.reason, idempotency_key),
        )
        return DraftOut.model_validate(draft)

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
        version = await c.mode_commands.publish(
            mode_key,
            draft_revision=body.draft_revision,
            ctx=_ctx(principal, body.reason, idempotency_key),
        )
        return ModeConfigVersion.model_validate(version)

    @router.post(
        "/mode-configs/{mode_key}/asset-requests",
        response_model=AssetRequestOut,
        status_code=201,
        summary="Запросить ассеты под загадку черновика",
        description=(
            "Если под `params_hash(clue.params)` черновика нет профиля ассетов — событие "
            "`game.ClueAssetsRequested` (ADR 0025). Повтор с тем же ключом — тот же ответ."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501, 503),
    )
    async def request_assets(
        mode_key: ModeKeyPath,
        body: AssetRequestCreate,
        principal: ConfigPublisher,
        idempotency_key: IdempotencyKey,
    ) -> AssetRequestOut:
        raise NotImplementedYetError("этап 3a: поток B (docs/plans/stage-3a.md)")

    @router.get(
        "/mode-configs/{mode_key}/asset-requests",
        response_model=list[AssetRequestOut],
        summary="Запросы ассетов режима",
        responses=problem_responses(401, 403, 404, 422, 501),
    )
    async def list_asset_requests(
        mode_key: ModeKeyPath, principal: ConfigViewer
    ) -> list[AssetRequestOut]:
        raise NotImplementedYetError("этап 3a: поток B (docs/plans/stage-3a.md)")

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
        version = await c.mode_commands.rollback(
            mode_key, to_version=body.to_version, ctx=_ctx(principal, body.reason, idempotency_key)
        )
        return ModeConfigVersion.model_validate(version)

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
        summary = await c.mode_commands.archive(
            mode_key, ctx=_ctx(principal, body.reason, idempotency_key)
        )
        return ModeConfigSummary.model_validate(summary)

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
        rows = await c.daily_admin.list_sets(mode_key, date_from, date_to)
        return [DailySet.model_validate(r) for r in rows]

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
        daily = await c.daily_admin.replace(
            mode_key,
            day,
            index,
            body.media_item_id,
            ctx=_ctx(principal, body.reason, idempotency_key),
        )
        return DailySet.model_validate(daily)

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
        daily = await c.daily_admin.regenerate(
            mode_key, day, ctx=_ctx(principal, body.reason, idempotency_key)
        )
        return DailySet.model_validate(daily)

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
        rows = await c.daily_admin.search(
            q=q, domain=domain, playable_for_mode=playable_for_mode, limit=limit
        )
        return [ProjectionItem.model_validate(r) for r in rows]

    return router
