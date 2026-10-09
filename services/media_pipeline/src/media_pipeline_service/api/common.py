"""Общее для роутеров: модели ответов, 410 для выключенных ассетов, описание ошибок."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from gv_service.problems import problem
from media_pipeline_service.application.dto import AssetSetView
from media_pipeline_service.domain.errors import AssetBlockedError


class Problem(BaseModel):
    """RFC 9457 Problem Details (поле ``code`` — машинный код ошибки)."""

    type: str
    title: str
    status: int
    code: str
    detail: str | None = None
    correlation_id: str | None = None


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """Описание ответов-ошибок для OpenAPI."""
    return {s: {"model": Problem, "description": "Problem Details"} for s in statuses}


class AssetSetItem(BaseModel):
    """Набор ассетов."""

    asset_set_id: UUID
    media_item_id: UUID
    clue_type: str
    profile_key: str
    params_hash: str
    status: str
    levels_count: int
    generation: int
    error_code: str | None

    @classmethod
    def of(cls, view: AssetSetView) -> AssetSetItem:
        """Из DTO use-case."""
        return cls(
            asset_set_id=view.asset_set_id,
            media_item_id=view.media_item_id,
            clue_type=view.clue_type,
            profile_key=view.profile_key,
            params_hash=view.params_hash,
            status=view.status,
            levels_count=view.levels_count,
            generation=view.generation,
            error_code=view.error_code,
        )


async def gone_on_blocked[T](call: Callable[[], Awaitable[T]]) -> T | JSONResponse:
    """Выполнить и отобразить ``AssetBlockedError`` в 410 Gone.

    В ``gv_service.problems`` нет класса ошибок для 410 (запрос оркестратору).
    """
    try:
        return await call()
    except AssetBlockedError as exc:
        return problem(410, exc.code, exc.message)
