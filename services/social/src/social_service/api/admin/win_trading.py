"""Очередь подозрительных пар и серий сдач: /admin/v1/win-trading (ADR 0068).

Права `admin.access` + `social.moderate`. Только чтение: санкций и уведомлений игроку нет,
данные видны одному модератору. Ответы - `Cache-Control: no-store`.
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки права.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from gv_auth import Principal, require_perm
from gv_core.errors import DependencyUnavailableError
from gv_service import problem_responses
from social_service.api.admin.services import AdminServices
from social_service.api.schemas import Cursor, WinTradingKind, WinTradingPage, WinTradingSeverity

NO_STORE = "no-store"
Moderator = Annotated[Principal, Depends(require_perm("admin.access", "social.moderate"))]


def register_win_trading_routes(router: APIRouter, services: AdminServices) -> None:
    """Добавить в `/admin/v1` очередь сигналов win-trading."""

    @router.get(
        "/win-trading/signals",
        response_model=WinTradingPage,
        summary="Подозрительные пары и серии сдач",
        description=(
            "Сигналы детектора win-trading (ADR 0068): повторы пары, сдачи подряд и за окно, "
            "с числами и окном конфига. Свежие первыми; фильтры по виду, серьёзности и игроку. "
            "Сигнал - повод посмотреть, не вердикт: друзья тоже играют друг с другом."
        ),
        responses=problem_responses(401, 403, 422),
    )
    async def signals(
        moderator: Moderator,
        response: Response,
        *,
        kind: Annotated[WinTradingKind | None, Query()] = None,
        severity: Annotated[WinTradingSeverity | None, Query()] = None,
        user_id: Annotated[UUID | None, Query(description="Игрок на любой стороне")] = None,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> WinTradingPage:
        service = services.win_trading
        if service is None:
            raise DependencyUnavailableError("детектор win-trading не подключён")
        data = await service.queue(
            kind=kind, severity=severity, user_id=user_id, cursor=cursor, limit=limit
        )
        response.headers["Cache-Control"] = NO_STORE
        return WinTradingPage.model_validate(data)
