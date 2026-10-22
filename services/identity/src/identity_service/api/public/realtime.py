"""Connection-токен Centrifugo: ``POST /v1/realtime/token`` (план этапа 4, D9, I1; ADR 0035).

Шаг 0a: контракт и заглушка (501 ``common.not_implemented`` после авторизации);
реализация — поток I. Зона повышенной безопасности (CLAUDE.md, правило 9).

Будущий токен: JWT ``sub`` = user id, ``exp`` ≤ 10 мин, ``aud = centrifugo``, без
``perms``/``channels`` (подписку на комнату выдаёт match своим секретом); подпись —
отдельным секретом Centrifugo, а не ключами access-токенов, чтобы токен не принимался
сервисами как access. Только пользователь (гость — 403 ``auth.guest_forbidden``, D13);
cookie-запрос — Origin из ``GV_PUBLIC_ORIGINS`` и ``X-CSRF-Token``; лимит частоты.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from gv_auth import Principal, require_same_origin
from gv_core.errors import NotImplementedYetError
from gv_service import problem_responses
from identity_service.api.common import ApiDeps


class RealtimeTokenResponse(BaseModel):
    """Connection-токен Centrifugo (клиент передаёт его в ``centrifuge``)."""

    token: str
    expires_at: datetime = Field(description="Истечение токена (≤ 10 мин от выдачи)")


def build_realtime_router(deps: ApiDeps) -> APIRouter:
    """Ручка выдачи connection-токена realtime."""
    router = APIRouter(prefix="/v1", tags=["identity"])
    # Authenticator.user: 401 без токена, 403 гостю; для cookie — Origin и CSRF (gv_auth.csrf).
    User = Annotated[Principal, Depends(deps.auth.user)]  # noqa: N806

    @router.post(
        "/realtime/token",
        response_model=RealtimeTokenResponse,
        dependencies=[Depends(require_same_origin)],
        summary="Connection-токен realtime (Centrifugo)",
        description=(
            "Только пользователь с аккаунтом: гость — 403 `auth.guest_forbidden`, отозванная "
            "сессия — 401 `auth.session_revoked`. Cookie-запрос — Origin из "
            "`GV_PUBLIC_ORIGINS` и `X-CSRF-Token` (403 `auth.csrf_failed`). Лимит частоты на "
            "пользователя (429). Ответ — `Cache-Control: no-store`."
        ),
        responses=problem_responses(401, 403, 429, 501),
    )
    async def realtime_token(principal: User) -> RealtimeTokenResponse:  # noqa: ARG001
        raise NotImplementedYetError("realtime-токен — поток I плана этапа 4")

    return router
