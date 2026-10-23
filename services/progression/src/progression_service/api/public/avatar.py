"""Своя аватарка (5b, поток S3): ``GET|POST /v1/me/avatar`` (ADR 0048).

* только пользователь с аккаунтом (гость - 403 ``auth.guest_forbidden``);
* заявка требует купленного товара «своя аватарка» (403 ``progression.avatar_not_entitled``);
* файл проверяет media-pipeline (сигнатура, размер, стороны, пересжатие, EXIF); здесь - только
  жёсткий лимит чтения тела до передачи и маппинг ошибок;
* ответы - ``Cache-Control: private, no-store``.
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки доступа.
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Response, UploadFile

from gv_auth import Principal, require_user
from gv_service import IdempotencyKey, problem_responses
from gv_service.problems import problem
from progression_service.api.public.shop_schemas import AvatarLimits, AvatarRequestView, MyAvatar
from progression_service.application.shop import ShopServices
from progression_service.domain.avatar import AvatarRequest, AvatarTooLargeError
from progression_service.settings import ProgressionSettings

User = Annotated[Principal, Depends(require_user)]
PRIVATE = "private, no-store"
_READ_CHUNK = 64 * 1024


async def _read_limited(file: UploadFile, max_bytes: int) -> bytes:
    """Прочитать тело не больше лимита: лишнее не попадает в память целиком."""
    if file.size is not None and file.size > max_bytes:
        raise AvatarTooLargeError(max_bytes)
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(_READ_CHUNK):
        size += len(chunk)
        if size > max_bytes:
            raise AvatarTooLargeError(max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


def _view(request: AvatarRequest) -> AvatarRequestView:
    return AvatarRequestView(
        asset_id=request.asset_id,
        status=request.status.value,
        submitted_at=request.submitted_at,
        reason_code=request.reason_code,
    )


def register_avatar_routes(
    router: APIRouter, services: ShopServices, settings: ProgressionSettings
) -> None:
    """Добавить в ``/v1`` ручки своей аватарки."""
    limits = AvatarLimits(
        max_bytes=settings.avatar_max_bytes,
        formats=list(settings.avatar_formats),
        min_side_px=settings.avatar_min_side_px,
        max_side_px=settings.avatar_max_side_px,
    )

    @router.get(
        "/me/avatar",
        response_model=MyAvatar,
        summary="Моя аватарка",
        description=(
            "Куплено ли право, действующий аватар, последняя заявка и лимиты загрузки "
            "(лимиты - из настроек progression, дублируют конфиг media-pipeline)."
        ),
        responses=problem_responses(401, 403),
    )
    async def my_avatar(user: User, response: Response) -> MyAvatar:
        state = await services.avatars.mine(user.user_id)
        response.headers["Cache-Control"] = PRIVATE
        return MyAvatar(
            entitled=state.entitled,
            current_url=services.ugc_url(state.current.public_key) if state.current else None,
            request=_view(state.request) if state.request else None,
            limits=limits,
        )

    @router.post(
        "/me/avatar",
        response_model=AvatarRequestView,
        status_code=202,
        summary="Загрузить свою аватарку",
        description=(
            "multipart, поле `file`. Нужен купленный товар "
            "(403 `progression.avatar_not_entitled`). "
            "Файл уходит в media-pipeline (проверка, пересжатие, UGC-ассет `pending`); до решения "
            "модератора виден прежний аватар. Новая заявка отменяет предыдущую `pending`. "
            "Повтор с тем же `Idempotency-Key` возвращает ту же заявку. "
            "Ошибки файла: 422 `ugc.invalid_image`, 413 `ugc.too_large`; media-pipeline "
            "недоступен - 503 `progression.avatar_unavailable`."
        ),
        responses=problem_responses(401, 403, 409, 413, 422, 503),
    )
    async def submit_avatar(
        user: User,
        key: IdempotencyKey,
        response: Response,
        file: Annotated[UploadFile, File(description="Изображение")],
    ) -> AvatarRequestView | Response:
        try:
            data = await _read_limited(file, settings.avatar_max_bytes)
            request = await services.avatars.submit(user.user_id, data, key)
        except AvatarTooLargeError as exc:
            too_large = problem(
                413,
                exc.code,
                "Файл больше допустимого размера",
                extra={"details": exc.details},
            )
            too_large.headers["Cache-Control"] = PRIVATE
            return too_large
        response.headers["Cache-Control"] = PRIVATE
        return _view(request)
