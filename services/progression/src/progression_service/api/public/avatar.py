"""Своя аватарка (5b, поток S3): ``GET|POST /v1/me/avatar``.

Шаг S1: ручки остаются заглушками 501 из шага 0a (контракт ADR 0048); реализацию делает
поток S3 (``application/avatars``, ``AssetModerated``).
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки доступа.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from gv_auth import Principal, require_user
from gv_core.errors import NotImplementedYetError
from gv_service import IdempotencyKey, problem_responses
from progression_service.api.public.shop_schemas import AvatarRequestView, MyAvatar

User = Annotated[Principal, Depends(require_user)]


def _stub() -> NotImplementedYetError:
    return NotImplementedYetError("ручка появится в потоке S3 этапа 5b")


def register_avatar_routes(router: APIRouter) -> None:
    """Добавить в ``/v1`` ручки аватарки (пока заглушки)."""

    @router.get(
        "/me/avatar",
        response_model=MyAvatar,
        summary="Моя аватарка",
        description="Куплено ли право, действующий аватар, последняя заявка и лимиты загрузки.",
        responses=problem_responses(401, 403, 501),
    )
    async def my_avatar(user: User) -> MyAvatar:
        raise _stub()

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
            "Ошибки файла: 422 `ugc.invalid_image`, 413 `ugc.too_large`."
        ),
        responses=problem_responses(401, 403, 413, 422, 501),
    )
    async def submit_avatar(
        user: User,
        key: IdempotencyKey,
        file: Annotated[UploadFile, File(description="Изображение")],
    ) -> AvatarRequestView:
        raise _stub()
