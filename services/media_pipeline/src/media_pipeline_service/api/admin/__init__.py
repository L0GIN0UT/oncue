"""Admin API (/admin/v1): для админ-панели, только с правами модератора/администратора.

Единые правила admin-API: чтение — ``admin.access``; мутация — ``admin.access`` + конкретное
право, обязательные ``Idempotency-Key`` (``gv_service.IdempotencyKey``) и ``reason``
(``gv_service.Reason``), аудит в ``admin_actions``. Ключ и ответ мутации сохраняются в её же
транзакции (``gv_db.idempotency``).
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field

from gv_auth import Principal, require_perm
from gv_service import IdempotencyKey, Reason, problem_responses
from media_pipeline_service.api.common import AssetSetItem
from media_pipeline_service.application.commands.generation import RegenerateAssetSet
from media_pipeline_service.application.commands.sources import SetSourcePosition, UploadSource
from media_pipeline_service.application.dto import SourceProvenance
from media_pipeline_service.application.queries.asset_sets import ListAssetSets
from media_pipeline_service.application.queries.profiles import ListProfiles
from media_pipeline_service.domain.errors import SourceTooLargeError
from media_pipeline_service.domain.sources import SourceKind, SourceOrigin, SourceStatus

# Виды, которые админ загружает файлом (``text`` приходит из catalog, файлом не грузится).
UploadKind = Literal["audio", "frame", "poster", "soundtrack", "audio_clip"]
UploadOrigin = Literal["upload", "seed", "tmdb"]

_READ_CHUNK = 1024 * 1024

Viewer = Annotated[Principal, Depends(require_perm("admin.access"))]
Uploader = Annotated[Principal, Depends(require_perm("admin.access", "media.upload_source"))]
Regenerator = Annotated[Principal, Depends(require_perm("admin.access", "media.regenerate"))]


class RegenerateRequest(BaseModel):
    """Перегенерация набора."""

    reason: Reason


class UploadResponse(BaseModel):
    """Загруженный исходник и наборы, поставленные в генерацию."""

    source_media_id: UUID
    media_item_id: UUID
    kind: SourceKind
    sha256: str
    bytes: int
    mime: str
    scheduled_asset_set_ids: list[UUID]
    position: int = Field(ge=1, le=100)
    origin: SourceOrigin


class SourceOut(BaseModel):
    """Исходник после смены порядка и наборы, поставленные в (пере)генерацию."""

    source_media_id: UUID
    media_item_id: UUID
    kind: SourceKind
    origin: SourceOrigin
    position: int = Field(ge=1, le=100)
    status: SourceStatus
    scheduled_asset_set_ids: list[UUID]


class ProfileSets(BaseModel):
    """Сколько наборов профиля в каком состоянии."""

    ready: int = Field(ge=0)
    pending: int = Field(ge=0)
    failed: int = Field(ge=0)


class ProfileOut(BaseModel):
    """Профиль ассетов: статический (``asset_profiles.yaml``) или динамический (ADR 0025)."""

    clue_type: str
    profile_key: str | None = Field(
        description="null у динамического профиля (в наборах и AssetsReady — dyn_<16 hex хэша>)"
    )
    params_hash: str
    origin: Literal["static", "dynamic"]
    status: Literal["active", "backfilling", "rejected"]
    sets: ProfileSets
    domains: list[Literal["music", "film"]] = Field(default_factory=list)
    error_code: str | None = Field(
        default=None,
        description=(
            "Почему отклонён динамический профиль: profile.invalid, profile.unknown_clue_type, "
            "profile.hash_mismatch, profile.limit_reached, profile.params_too_large"
        ),
    )


class SourcePositionRequest(BaseModel):
    """Порядок исходника своего вида (кадры ``frame_sequence``: 1 — самый сложный)."""

    position: int = Field(ge=1, le=100)
    reason: Reason


async def _read_limited(file: UploadFile, max_bytes: int) -> bytes:
    """Прочитать файл не больше лимита (чтение порциями, без загрузки «бомбы» в память)."""
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(_READ_CHUNK):
        size += len(chunk)
        if size > max_bytes:
            raise SourceTooLargeError("файл больше лимита", details={"max_bytes": max_bytes})
        chunks.append(chunk)
    return b"".join(chunks)


def build_admin_router(
    *,
    upload: UploadSource,
    set_position: SetSourcePosition,
    regenerate: RegenerateAssetSet,
    list_sets: ListAssetSets,
    list_profiles: ListProfiles,
    max_upload_bytes: int,
) -> APIRouter:
    """Ручки админки: загрузка исходников, перегенерация, просмотр наборов."""
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    @router.post(
        "/sources",
        status_code=201,
        response_model=UploadResponse,
        summary="Загрузить исходник (аудио, кадр, постер, саундтрек, звук фильма) для единицы",
        description=(
            "Вне local/ci обязательны origin, source_url и license_note (D6 плана этапа 3a), "
            "иначе 422 source.provenance_required. position — порядок среди исходников вида "
            "(кадры frame_sequence: 1 — самый сложный)."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 503),
    )
    async def upload_source(
        *,
        principal: Uploader,
        key: IdempotencyKey,
        media_item_id: Annotated[UUID, Form()],
        kind: Annotated[UploadKind, Form()],
        reason: Annotated[Reason, Form()],
        file: Annotated[UploadFile, File(description="Файл исходника")],
        position: Annotated[int, Form(ge=1, le=100)] = 1,
        origin: Annotated[UploadOrigin | None, Form()] = None,
        source_url: Annotated[str | None, Form(min_length=1, max_length=2000)] = None,
        license_note: Annotated[str | None, Form(min_length=1, max_length=500)] = None,
    ) -> UploadResponse:
        data = await _read_limited(file, max_upload_bytes)
        result = await upload.execute(
            media_item_id=media_item_id,
            kind=SourceKind(kind),
            data=data,
            actor_id=principal.user_id,
            reason=reason,
            idempotency_key=key,
            position=position,
            provenance=SourceProvenance(
                origin=SourceOrigin(origin) if origin else None,
                source_url=source_url,
                license_note=license_note,
            ),
        )
        return UploadResponse(
            source_media_id=result.source_media_id,
            media_item_id=result.media_item_id,
            kind=result.kind,
            sha256=result.sha256,
            bytes=result.size_bytes,
            mime=result.mime,
            scheduled_asset_set_ids=list(result.scheduled_asset_set_ids),
            position=result.position,
            origin=result.origin,
        )

    @router.post(
        "/asset-sets/{asset_set_id}/regenerate",
        status_code=202,
        response_model=AssetSetItem,
        summary="Перегенерировать набор ассетов",
        responses=problem_responses(401, 403, 404, 409, 410, 422),
    )
    async def regenerate_asset_set(
        asset_set_id: UUID,
        body: RegenerateRequest,
        principal: Regenerator,
        key: IdempotencyKey,
    ) -> AssetSetItem:
        view = await regenerate.execute(
            asset_set_id, actor_id=principal.user_id, reason=body.reason, idempotency_key=key
        )
        return AssetSetItem.of(view)

    @router.get(
        "/profiles",
        response_model=list[ProfileOut],
        summary="Профили ассетов и готовность их наборов",
        responses=problem_responses(401, 403, 422),
    )
    async def admin_profiles(
        _: Viewer,
        clue_type: Annotated[str | None, Query(max_length=64, pattern=r"^[a-z][a-z0-9_]*$")] = None,
        params_hash: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None,
    ) -> list[ProfileOut]:
        views = await list_profiles.execute(clue_type, params_hash)
        return [
            ProfileOut(
                clue_type=v.clue_type,
                profile_key=v.profile_key,
                params_hash=v.params_hash,
                origin=v.origin.value,
                status=v.status,
                sets=ProfileSets(ready=v.ready, pending=v.pending, failed=v.failed),
                domains=list(v.domains),
                error_code=v.error_code,
            )
            for v in views
        ]

    @router.put(
        "/sources/{source_media_id}/position",
        response_model=SourceOut,
        summary="Изменить порядок исходника",
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def set_source_position(
        source_media_id: UUID,
        body: SourcePositionRequest,
        principal: Uploader,
        key: IdempotencyKey,
    ) -> SourceOut:
        view = await set_position.execute(
            source_media_id,
            position=body.position,
            actor_id=principal.user_id,
            reason=body.reason,
            idempotency_key=key,
        )
        return SourceOut(
            source_media_id=view.source_media_id,
            media_item_id=view.media_item_id,
            kind=view.kind,
            origin=view.origin,
            position=view.position,
            status=SourceStatus(view.status),
            scheduled_asset_set_ids=list(view.scheduled_asset_set_ids),
        )

    @router.get(
        "/asset-sets",
        response_model=list[AssetSetItem],
        summary="Наборы ассетов единицы (для карточки в админке)",
        responses=problem_responses(401, 403, 422),
    )
    async def admin_asset_sets(media_item_id: UUID, _: Viewer) -> list[AssetSetItem]:
        return [AssetSetItem.of(v) for v in await list_sets.execute(media_item_id)]

    return router
