"""Admin API (/admin/v1): для админ-панели, только с правами модератора/администратора.

Единые правила admin-API: чтение — ``admin.access``; мутация — ``admin.access`` + конкретное
право, обязательные ``Idempotency-Key`` (``gv_service.IdempotencyKey``) и ``reason``
(``gv_service.Reason``), аудит в ``admin_actions``. Ключ и ответ мутации сохраняются в её же
транзакции (``gv_db.idempotency``).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel

from gv_auth import Principal, require_perm
from gv_service import IdempotencyKey, Reason, problem_responses
from media_pipeline_service.api.common import AssetSetItem
from media_pipeline_service.application.commands.generation import RegenerateAssetSet
from media_pipeline_service.application.commands.sources import UploadSource
from media_pipeline_service.application.queries.asset_sets import ListAssetSets
from media_pipeline_service.domain.errors import SourceTooLargeError
from media_pipeline_service.domain.sources import SourceKind

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
    regenerate: RegenerateAssetSet,
    list_sets: ListAssetSets,
    max_upload_bytes: int,
) -> APIRouter:
    """Ручки админки: загрузка исходников, перегенерация, просмотр наборов."""
    router = APIRouter(prefix="/admin/v1", tags=["admin"])

    @router.post(
        "/sources",
        status_code=201,
        response_model=UploadResponse,
        summary="Загрузить исходник (аудио, кадр или постер) для единицы",
        responses=problem_responses(401, 403, 404, 409, 422, 503),
    )
    async def upload_source(
        *,
        principal: Uploader,
        key: IdempotencyKey,
        media_item_id: Annotated[UUID, Form()],
        kind: Annotated[SourceKind, Form()],
        reason: Annotated[Reason, Form()],
        file: Annotated[UploadFile, File(description="Файл исходника")],
    ) -> UploadResponse:
        data = await _read_limited(file, max_upload_bytes)
        result = await upload.execute(
            media_item_id=media_item_id,
            kind=kind,
            data=data,
            actor_id=principal.user_id,
            reason=reason,
            idempotency_key=key,
        )
        return UploadResponse(
            source_media_id=result.source_media_id,
            media_item_id=result.media_item_id,
            kind=result.kind,
            sha256=result.sha256,
            bytes=result.size_bytes,
            mime=result.mime,
            scheduled_asset_set_ids=list(result.scheduled_asset_set_ids),
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
        "/asset-sets",
        response_model=list[AssetSetItem],
        summary="Наборы ассетов единицы (для карточки в админке)",
        responses=problem_responses(401, 403, 422),
    )
    async def admin_asset_sets(media_item_id: UUID, _: Viewer) -> list[AssetSetItem]:
        return [AssetSetItem.of(v) for v in await list_sets.execute(media_item_id)]

    return router
