"""Admin API (/admin/v1): для админ-панели, только с правами модератора/администратора.

Каждая мутация: ``require_perm``, обязательные ``Idempotency-Key`` и ``reason``, аудит
(таблица ``admin_actions``) — §2.5 и §8 п.7 плана.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from gv_auth import Principal, require_perm
from media_pipeline_service.api.common import AssetSetItem, gone_on_blocked, problem_responses
from media_pipeline_service.application.commands.generation import RegenerateAssetSet
from media_pipeline_service.application.commands.sources import UploadSource
from media_pipeline_service.application.idempotency import IdempotentExecutor, validate_key
from media_pipeline_service.application.queries.asset_sets import ListAssetSets
from media_pipeline_service.domain.errors import SourceTooLargeError
from media_pipeline_service.domain.sources import SourceKind

_READ_CHUNK = 1024 * 1024
Reason = Annotated[str, Field(min_length=3, max_length=500)]


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


def _hash(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()


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
    idempotent: IdempotentExecutor,
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
        principal: Annotated[Principal, Depends(require_perm("media.upload_source"))],
        media_item_id: Annotated[UUID, Form()],
        kind: Annotated[SourceKind, Form()],
        reason: Annotated[str, Form(min_length=3, max_length=500)],
        file: Annotated[UploadFile, File(description="Файл исходника")],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JSONResponse:
        key = validate_key(idempotency_key)
        data = await _read_limited(file, max_upload_bytes)

        async def action() -> tuple[int, dict[str, Any]]:
            result = await upload.execute(
                media_item_id=media_item_id,
                kind=kind,
                data=data,
                actor_id=principal.user_id,
                reason=reason,
            )
            body = UploadResponse(
                source_media_id=result.source_media_id,
                media_item_id=result.media_item_id,
                kind=result.kind,
                sha256=result.sha256,
                bytes=result.size_bytes,
                mime=result.mime,
                scheduled_asset_set_ids=list(result.scheduled_asset_set_ids),
            )
            return 201, body.model_dump(mode="json")

        status, body = await idempotent.run(
            scope=f"{principal.user_id}:sources",
            key=key,
            request_hash=_hash(
                str(media_item_id), kind.value, reason, hashlib.sha256(data).hexdigest()
            ),
            action=action,
        )
        return JSONResponse(body, status_code=status)

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
        principal: Annotated[Principal, Depends(require_perm("media.regenerate"))],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JSONResponse:
        key = validate_key(idempotency_key)

        async def action() -> tuple[int, dict[str, Any]]:
            view = await regenerate.execute(
                asset_set_id, actor_id=principal.user_id, reason=body.reason
            )
            return 202, AssetSetItem.of(view).model_dump(mode="json")

        async def run() -> tuple[int, dict[str, Any]]:
            return await idempotent.run(
                scope=f"{principal.user_id}:regenerate:{asset_set_id}",
                key=key,
                request_hash=_hash(str(asset_set_id), body.reason),
                action=action,
            )

        result = await gone_on_blocked(run)
        if isinstance(result, JSONResponse):
            return result
        status, payload = result
        return JSONResponse(payload, status_code=status)

    @router.get(
        "/asset-sets",
        response_model=list[AssetSetItem],
        summary="Наборы ассетов единицы (для карточки в админке)",
        responses=problem_responses(401, 403),
    )
    async def admin_asset_sets(
        media_item_id: UUID,
        _: Annotated[Principal, Depends(require_perm("admin.access"))],
    ) -> list[AssetSetItem]:
        return [AssetSetItem.of(v) for v in await list_sets.execute(media_item_id)]

    return router
