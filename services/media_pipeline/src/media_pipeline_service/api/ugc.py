"""Схемы и ручки UGC-изображений (аватарки, обложки сборников): приём и модерация.

Контракт этапа 5b (``docs/plans/stage-5.md`` §9.6, ADR 0048):

* приём - internal (``POST /internal/v1/ugc-images``), сервисный токен с областью ``ugc.image``:
  вызывает progression, пользовательского доступа к media-pipeline нет; клиент определяется
  по ``sub = svc:<id>`` токена и должен быть в ``allowed_clients`` назначения;
* модерация - admin (``/admin/v1/ugc-images``), право ``media.ugc.moderate`` (moderator, admin);
  решение с причиной из списка кодов конфига пишет аудит и публикует ``AssetModerated``;
* ассет ``pending`` и ``rejected`` недоступен по публичному адресу;
* успешные ответы - ``Cache-Control: no-store``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from gv_auth import Principal, ServiceAuthenticator, ServicePrincipal, require_perm
from gv_service import IdempotencyKey, Reason, problem_responses
from gv_service.problems import problem
from media_pipeline_service.application.commands.ugc import UgcUseCases
from media_pipeline_service.application.ugc import UgcImageView
from media_pipeline_service.domain.errors import UgcClientForbiddenError, UgcTooLargeError
from media_pipeline_service.domain.ugc import UgcStatus

UGC_IMAGE_SCOPE = "ugc.image"
"""Область сервисного токена для приёма UGC-изображений (``GV_SERVICE_CLIENTS`` identity)."""

Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
UgcStatusName = Literal["pending", "approved", "rejected"]
Cursor = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,512}$")]
AssetIdPath = Annotated[UUID, Path(description="Идентификатор UGC-ассета")]

Moderator = Annotated[Principal, Depends(require_perm("admin.access", "media.ugc.moderate"))]
Caller = Annotated[
    ServicePrincipal, Depends(ServiceAuthenticator().require_service(UGC_IMAGE_SCOPE))
]

_READ_CHUNK = 64 * 1024
_NO_STORE = "no-store"


class UgcImage(BaseModel):
    """UGC-изображение."""

    asset_id: UUID
    owner_user_id: UUID
    purpose: Key = Field(description="Назначение из конфига: avatar, collection_cover")
    status: UgcStatusName
    reason_code: Key | None = Field(description="Код причины отклонения; иначе null")
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    bytes: int = Field(ge=1)
    submitted_at: datetime
    decided_at: datetime | None
    moderator_id: UUID | None
    version: int = Field(ge=1, description="Монотонная версия решения (как в `AssetModerated`)")

    @classmethod
    def of(cls, view: UgcImageView) -> UgcImage:
        """Из результата use-case."""
        return cls(
            asset_id=view.asset_id,
            owner_user_id=view.owner_user_id,
            purpose=view.purpose,
            status=view.status,
            reason_code=view.reason_code,
            width=view.width,
            height=view.height,
            bytes=view.size_bytes,
            submitted_at=view.submitted_at,
            decided_at=view.decided_at,
            moderator_id=view.moderator_id,
            version=view.version,
        )


class UgcImagePage(BaseModel):
    """Страница очереди (курсор), старые заявки первыми."""

    items: list[UgcImage]
    next_cursor: Cursor | None


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


def _no_store(response: Response) -> None:
    """Зависимость: ``Cache-Control: no-store`` на успешных ответах."""
    response.headers["Cache-Control"] = _NO_STORE


async def _read_limited(file: UploadFile, max_bytes: int) -> bytes:
    """Прочитать файл не больше лимита: тело отбрасывается до декодирования."""
    if file.size is not None and file.size > max_bytes:
        raise UgcTooLargeError("файл больше лимита", details={"max_bytes": max_bytes})
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(_READ_CHUNK):
        size += len(chunk)
        if size > max_bytes:
            raise UgcTooLargeError("файл больше лимита", details={"max_bytes": max_bytes})
        chunks.append(chunk)
    return b"".join(chunks)


def _too_large(max_bytes: int) -> Response:
    """413 Problem Details (в общем обработчике ошибок такого статуса нет)."""
    reply = problem(
        413,
        "ugc.too_large",
        "Файл больше допустимого размера",
        extra={"details": {"max_bytes": max_bytes}},
    )
    reply.headers["Cache-Control"] = _NO_STORE
    return reply


def register_ugc_internal_routes(router: APIRouter, ugc: UgcUseCases) -> None:
    """Добавить в ``/internal/v1`` приём UGC-изображений."""

    @router.post(
        "/ugc-images",
        response_model=UgcImage,
        status_code=202,
        summary="Принять UGC-изображение",
        description=(
            "multipart: `file`, `owner_user_id`, `purpose`. Клиент определяется по сервисному "
            "токену и должен быть в `allowed_clients` назначения (иначе 403). Проверка по "
            "сигнатуре (не `Content-Type`), размеру и сторонам, только статичные "
            "JPEG/PNG/WebP; пересжатие в квадратный WebP и удаление EXIF, объект в S3 под "
            "случайным ключом; ассет создаётся `pending`. Повтор с тем же `Idempotency-Key` "
            "для владельца возвращает тот же ассет без повторной обработки. Новая заявка "
            "владельца на то же назначение тихо отменяет прежнюю `pending` (событие не "
            "публикуется). Ошибки: 422 `ugc.invalid_image`, 413 `ugc.too_large`, "
            "422 `ugc.purpose_unknown`, 403 `ugc.client_forbidden`."
        ),
        responses=problem_responses(401, 403, 409, 413, 422, 503),
        dependencies=[Depends(_no_store)],
    )
    async def submit(
        caller: Caller,
        key: IdempotencyKey,
        owner_user_id: Annotated[UUID, Form()],
        purpose: Annotated[Key, Form()],
        file: Annotated[UploadFile, File(description="Изображение")],
    ) -> UgcImage | Response:
        spec = ugc.catalog.purpose(purpose)
        # Чужой клиент не заставляет сервис читать тело.
        if caller.client_id not in spec.allowed_clients:
            raise UgcClientForbiddenError("сервису это назначение недоступно")
        try:
            data = await _read_limited(file, spec.max_bytes)
        except UgcTooLargeError:
            return _too_large(spec.max_bytes)
        try:
            view = await ugc.submit.execute(
                client_id=caller.client_id,
                owner_user_id=owner_user_id,
                purpose=purpose,
                data=data,
                idempotency_key=key,
            )
        except UgcTooLargeError:
            return _too_large(spec.max_bytes)
        return UgcImage.of(view)


def register_ugc_admin_routes(router: APIRouter, ugc: UgcUseCases) -> None:
    """Добавить в ``/admin/v1`` очередь «UGC-изображения»."""

    @router.get(
        "/ugc-images/settings",
        response_model=UgcSettings,
        summary="Назначения и причины отклонения",
        responses=problem_responses(401, 403),
        dependencies=[Depends(_no_store)],
    )
    async def settings(principal: Moderator) -> UgcSettings:  # noqa: ARG001 - только проверка права
        catalog = ugc.catalog
        return UgcSettings(
            purposes=[
                UgcLimits(
                    purpose=p.key,
                    max_bytes=p.max_bytes,
                    formats=list(p.input_formats),
                    min_side_px=p.min_side_px,
                    max_side_px=p.max_side_px,
                )
                for p in catalog.purposes.values()
            ],
            reasons=[UgcRejectionReason(code=r.code, title=r.title) for r in catalog.reasons],
        )

    @router.get(
        "/ugc-images",
        response_model=UgcImagePage,
        summary="Очередь UGC-изображений",
        description=(
            "Фильтры по статусу (по умолчанию `pending`) и назначению; старые заявки первыми. "
            "Некорректный курсор - 422 `ugc.cursor_invalid`."
        ),
        responses=problem_responses(401, 403, 422),
        dependencies=[Depends(_no_store)],
    )
    async def queue(
        principal: Moderator,  # noqa: ARG001 - только проверка права
        status: Annotated[UgcStatusName, Query()] = "pending",
        purpose: Annotated[Key | None, Query()] = None,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> UgcImagePage:
        page = await ugc.queue.execute(
            status=UgcStatus(status), purpose=purpose, cursor=cursor, limit=limit
        )
        return UgcImagePage(
            items=[UgcImage.of(v) for v in page.items], next_cursor=page.next_cursor
        )

    @router.get(
        "/ugc-images/{asset_id}",
        response_model=UgcImage,
        summary="Карточка UGC-изображения",
        description=(
            "Ассет в любом статусе: поля как у элемента очереди, включая решение, причину и "
            "версию. Ключей и адресов S3 нет. Нет ассета - 404 `ugc.not_found`."
        ),
        responses=problem_responses(401, 403, 404, 422),
        dependencies=[Depends(_no_store)],
    )
    async def card(asset_id: AssetIdPath, principal: Moderator) -> UgcImage:  # noqa: ARG001
        return UgcImage.of(await ugc.card.execute(asset_id))

    @router.get(
        "/ugc-images/{asset_id}/preview",
        response_class=Response,
        summary="Содержимое pending-изображения",
        description=(
            "Только для `pending` (ADR 0061): тело `image/webp` читается из хранилища по "
            "ключам сервиса, ссылок и адресов S3 в ответе нет. Заголовки: `Cache-Control: "
            "no-store`, `X-Content-Type-Options: nosniff`, `Content-Disposition: inline`. "
            "Выдача пишется в аудит. Не `pending` - 409 `ugc.not_pending`, нет ассета или "
            "объекта - 404 `ugc.not_found`."
        ),
        responses={
            **problem_responses(401, 403, 404, 409),
            200: {
                "description": "Изображение WebP",
                "content": {"image/webp": {"schema": {"type": "string", "format": "binary"}}},
            },
        },
    )
    async def preview(asset_id: AssetIdPath, principal: Moderator) -> Response:
        result = await ugc.preview.execute(asset_id, moderator_id=principal.user_id)
        return Response(
            content=result.data,
            media_type=result.content_type,
            headers={
                "Cache-Control": _NO_STORE,
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "inline",
            },
        )

    @router.post(
        "/ugc-images/{asset_id}/approve",
        response_model=UgcImage,
        summary="Одобрить",
        description=(
            "Только из `pending` (иначе 409 `ugc.not_pending`). В одной транзакции: статус, "
            "аудит и outbox `AssetModerated`; объект копируется в публичный `u/<hex>.webp`, "
            "`p/` удаляется после коммита."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
        dependencies=[Depends(_no_store)],
    )
    async def approve(
        asset_id: AssetIdPath, body: ApproveRequest, principal: Moderator, key: IdempotencyKey
    ) -> UgcImage:
        view = await ugc.moderate.approve(
            asset_id, moderator_id=principal.user_id, reason=body.reason, idempotency_key=key
        )
        return UgcImage.of(view)

    @router.post(
        "/ugc-images/{asset_id}/reject",
        response_model=UgcImage,
        summary="Отклонить или снять",
        description=(
            "Из `pending` (отклонение) и из `approved` (снятие UGC-изображения: версия растёт, "
            "публичный объект удаляется, `AssetModerated(rejected)`). Из `rejected` - 409 "
            "`ugc.not_pending`. `reason_code` - из списка конфига (422 `ugc.reason_unknown`); "
            "объект удаляется после коммита."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
        dependencies=[Depends(_no_store)],
    )
    async def reject(
        asset_id: AssetIdPath, body: RejectRequest, principal: Moderator, key: IdempotencyKey
    ) -> UgcImage:
        view = await ugc.moderate.reject(
            asset_id,
            moderator_id=principal.user_id,
            reason_code=body.reason_code,
            reason=body.reason,
            idempotency_key=key,
        )
        return UgcImage.of(view)
