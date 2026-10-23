"""Схемы и ручки UGC-изображений (аватарки, обложки сборников): приём и модерация.

Контракт этапа 5b (``docs/plans/stage-5.md`` §9.6, ADR 0048). Шаг 0a: ручки объявлены,
реализация - поток S3; до тех пор отвечают 501 ``common.not_implemented``.

* приём - internal (``POST /internal/v1/ugc-images``), сервисный токен с областью ``ugc.image``:
  вызывает progression, пользовательского доступа к media-pipeline нет;
* модерация - admin (``/admin/v1/ugc-images``), право ``media.ugc.moderate`` (moderator, admin);
  решение с причиной из списка кодов конфига пишет аудит и публикует ``AssetModerated``;
* ассет ``pending`` и ``rejected`` недоступен по публичному адресу.
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки доступа.
# ruff: noqa: ARG001
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from gv_auth import Principal, ServiceAuthenticator, ServicePrincipal, require_perm
from gv_core.errors import NotImplementedYetError
from gv_service import IdempotencyKey, Reason, problem_responses

UGC_IMAGE_SCOPE = "ugc.image"
"""Область сервисного токена для приёма UGC-изображений (``GV_SERVICE_CLIENTS`` identity)."""

Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
UgcStatus = Literal["pending", "approved", "rejected"]
Cursor = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,512}$")]
AssetIdPath = Annotated[UUID, Path(description="Идентификатор UGC-ассета")]

Moderator = Annotated[Principal, Depends(require_perm("admin.access", "media.ugc.moderate"))]
Caller = Annotated[
    ServicePrincipal, Depends(ServiceAuthenticator().require_service(UGC_IMAGE_SCOPE))
]


class UgcImage(BaseModel):
    """UGC-изображение."""

    asset_id: UUID
    owner_user_id: UUID
    purpose: Key = Field(description="Назначение из конфига: avatar, collection_cover")
    status: UgcStatus
    reason_code: Key | None = Field(description="Код причины отклонения; иначе null")
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    bytes: int = Field(ge=1)
    submitted_at: datetime
    decided_at: datetime | None
    moderator_id: UUID | None
    version: int = Field(ge=1, description="Монотонная версия решения (как в `AssetModerated`)")


class UgcImagePage(BaseModel):
    """Страница очереди (курсор), старые заявки первыми."""

    items: list[UgcImage]
    next_cursor: Cursor | None


class UgcImagePreview(BaseModel):
    """Короткая ссылка для просмотра модератором (в том числе `pending`)."""

    url: str
    expires_at: datetime


class UgcLimits(BaseModel):
    """Лимиты назначения (из конфига media-pipeline)."""

    purpose: Key
    max_bytes: int = Field(ge=1)
    formats: list[Key]
    min_side_px: int = Field(ge=1)
    max_side_px: int = Field(ge=1)


class UgcRejectionReason(BaseModel):
    """Код причины отклонения (конфиг; текст для игрока - шаблон notifications)."""

    code: Key
    title: str


class UgcSettings(BaseModel):
    """Назначения и причины отклонения для формы модератора."""

    purposes: list[UgcLimits]
    reasons: list[UgcRejectionReason]


class RejectRequest(BaseModel):
    """Отклонить изображение."""

    model_config = ConfigDict(extra="forbid")

    reason_code: Key
    reason: Reason


class ApproveRequest(BaseModel):
    """Одобрить изображение."""

    model_config = ConfigDict(extra="forbid")

    reason: Reason


def _stub() -> NotImplementedYetError:
    return NotImplementedYetError("ручка появится в потоке S3 этапа 5b")


def register_ugc_internal_routes(router: APIRouter) -> None:
    """Добавить в ``/internal/v1`` приём UGC-изображений (пока заглушка)."""

    @router.post(
        "/ugc-images",
        response_model=UgcImage,
        status_code=202,
        summary="Принять UGC-изображение",
        description=(
            "multipart: `file`, `owner_user_id`, `purpose`. Проверка по сигнатуре (не "
            "`Content-Type`), размеру и сторонам, пересжатие и удаление EXIF, объект в S3 под "
            "случайным ключом; ассет создаётся `pending`. Повтор с тем же `Idempotency-Key` "
            "возвращает тот же ассет. Новая заявка владельца на то же назначение отменяет "
            "прежнюю `pending`. Ошибки: 422 `ugc.invalid_image`, 413 `ugc.too_large`, "
            "422 `ugc.purpose_unknown`."
        ),
        responses=problem_responses(401, 403, 413, 422, 501),
    )
    async def submit(
        caller: Caller,
        key: IdempotencyKey,
        owner_user_id: Annotated[UUID, Form()],
        purpose: Annotated[Key, Form()],
        file: Annotated[UploadFile, File(description="Изображение")],
    ) -> UgcImage:
        raise _stub()


def register_ugc_admin_routes(router: APIRouter) -> None:
    """Добавить в ``/admin/v1`` очередь «UGC-изображения» (пока заглушки)."""

    @router.get(
        "/ugc-images/settings",
        response_model=UgcSettings,
        summary="Назначения и причины отклонения",
        responses=problem_responses(401, 403, 501),
    )
    async def settings(principal: Moderator) -> UgcSettings:
        raise _stub()

    @router.get(
        "/ugc-images",
        response_model=UgcImagePage,
        summary="Очередь UGC-изображений",
        description="Фильтры по статусу (по умолчанию `pending`) и назначению.",
        responses=problem_responses(401, 403, 422, 501),
    )
    async def queue(
        principal: Moderator,
        status: Annotated[UgcStatus, Query()] = "pending",
        purpose: Annotated[Key | None, Query()] = None,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> UgcImagePage:
        raise _stub()

    @router.get(
        "/ugc-images/{asset_id}/preview",
        response_model=UgcImagePreview,
        summary="Ссылка для просмотра",
        description="Короткая подписанная ссылка; выдача пишется в аудит.",
        responses=problem_responses(401, 403, 404, 501),
    )
    async def preview(asset_id: AssetIdPath, principal: Moderator) -> UgcImagePreview:
        raise _stub()

    @router.post(
        "/ugc-images/{asset_id}/approve",
        response_model=UgcImage,
        summary="Одобрить",
        description=(
            "Только из `pending` (иначе 409 `ugc.not_pending`). В одной транзакции: статус, "
            "аудит и outbox `AssetModerated`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501),
    )
    async def approve(
        asset_id: AssetIdPath, body: ApproveRequest, principal: Moderator, key: IdempotencyKey
    ) -> UgcImage:
        raise _stub()

    @router.post(
        "/ugc-images/{asset_id}/reject",
        response_model=UgcImage,
        summary="Отклонить",
        description=(
            "Только из `pending` (409 `ugc.not_pending`). `reason_code` - из списка конфига "
            "(422 `ugc.reason_unknown`); объект отклонённого ассета удаляется после коммита."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501),
    )
    async def reject(
        asset_id: AssetIdPath, body: RejectRequest, principal: Moderator, key: IdempotencyKey
    ) -> UgcImage:
        raise _stub()
