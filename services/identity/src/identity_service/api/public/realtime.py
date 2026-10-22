"""Connection-токен Centrifugo: ``POST /v1/realtime/token`` (план этапа 4, D9, I1; ADR 0035).

Зона повышенной безопасности (CLAUDE.md, правило 9). Токен: JWT HS256 на отдельном секрете
Centrifugo (не ключи access-токенов — сервисы его не примут), claims ``sub`` = user id,
``aud``, ``iat``, ``exp`` (``GV_REALTIME__TOKEN_TTL_S``, 10 мин); без ``channels``/``info``/
прав — подписку на комнату выдаёт match своим секретом. Только пользователь (гость — 403
``auth.guest_forbidden``); отозванный ``sid`` — 401 (denylist верификатора); cookie-запрос —
Origin из ``GV_PUBLIC_ORIGINS`` и ``X-CSRF-Token``; лимит частоты на пользователя.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from gv_auth import Principal, require_same_origin
from gv_core.errors import DependencyUnavailableError
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
            "пользователя (429). Нет секрета Centrifugo (только local/ci) — 503 "
            "`realtime.not_configured`. Ответ — `Cache-Control: no-store`."
        ),
        responses=problem_responses(401, 403, 429, 503),
    )
    async def realtime_token(principal: User, response: Response) -> RealtimeTokenResponse:
        issue = deps.use_cases.issue_realtime_token
        if issue is None:
            raise DependencyUnavailableError("realtime не настроен", code="realtime.not_configured")
        # Authenticator.user уже отсёк гостя; user_id у пользователя всегда есть.
        issued = await issue.execute(principal.user_id)
        # Токен — учётные данные: промежуточные узлы и браузер его не кэшируют.
        response.headers["Cache-Control"] = "no-store"
        return RealtimeTokenResponse(token=issued.token, expires_at=issued.expires_at)

    return router
