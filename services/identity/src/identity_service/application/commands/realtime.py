"""Выдача connection-токена Centrifugo (I1 плана этапа 4, D9, ADR 0035)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import DependencyUnavailableError
from identity_service.application.ports import RateLimiter
from identity_service.application.ports.realtime import RealtimeTokenSigner
from identity_service.application.security import ensure_allowed, rate_key

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RealtimeTokenConfig:
    """Параметры выдачи.

    Attributes:
        ttl_s: Время жизни токена (``GV_REALTIME__TOKEN_TTL_S``, по умолчанию 10 мин).
        audience: ``aud`` (``GV_REALTIME__TOKEN_AUDIENCE``; = ``client.token.audience``
            в конфиге Centrifugo).
        per_user: Выдач на пользователя за окно (``GV_REALTIME_TOKEN_LIMIT__PER_USER``).
        window_s: Окно лимита, секунд (``GV_REALTIME_TOKEN_LIMIT__WINDOW_S``).
    """

    ttl_s: int = 600
    audience: str = "centrifugo"
    per_user: int = 20
    window_s: int = 60


@dataclass(frozen=True, slots=True)
class IssuedRealtimeToken:
    """Подписанный connection-токен.

    Attributes:
        token: JWT; не логируется.
        expires_at: Момент истечения (``exp``).
    """

    token: str
    expires_at: datetime


def realtime_claims(
    user_id: UUID, *, now: datetime, ttl_s: int, audience: str
) -> dict[str, str | int]:
    """Claims connection-токена: только ``sub``, ``aud``, ``iat``, ``exp``.

    Без ``channels``/``subs`` (право на канал комнаты даёт subscription-токен match, D9),
    без ``info`` (в Centrifugo он виден другим участникам канала — ПДн туда не кладём), без
    прав и ``sid``. Время — целые секунды (NumericDate, RFC 7519).
    """
    iat = int(now.timestamp())
    return {"sub": str(user_id), "aud": audience, "iat": iat, "exp": iat + ttl_s}


class IssueRealtimeToken:
    """Выдать connection-токен Centrifugo пользователю с аккаунтом.

    Аутентификацию (сессия, отзыв ``sid``, гость → 403, Origin и CSRF) делает HTTP-слой
    до вызова. Здесь — лимит частоты на пользователя и подпись. Лимитер fail-open, как
    остальные лимиты identity: токен выдаётся только по действующему access-токену, а лимит
    защищает от зациклившегося клиента, а не от перебора — отказ Redis не должен отключать
    realtime всем.
    """

    def __init__(
        self,
        signer: RealtimeTokenSigner | None,
        limiter: RateLimiter,
        clock: Clock,
        config: RealtimeTokenConfig,
    ) -> None:
        self._signer = signer
        self._limiter = limiter
        self._clock = clock
        self._config = config

    async def execute(self, user_id: UUID) -> IssuedRealtimeToken:
        """Проверить лимит и подписать токен.

        Raises:
            DependencyUnavailableError: ``realtime.not_configured`` — нет секрета (503;
                возможно только в local/ci, вне них сервис не стартует).
            RateLimitedError: исчерпан лимит выдач пользователя (429).
        """
        if self._signer is None:
            raise DependencyUnavailableError("realtime не настроен", code="realtime.not_configured")
        ensure_allowed(
            await self._limiter.hit(
                rate_key("realtime_token", str(user_id)),
                limit=self._config.per_user,
                window_s=self._config.window_s,
            )
        )
        claims = realtime_claims(
            user_id,
            now=self._clock.now(),
            ttl_s=self._config.ttl_s,
            audience=self._config.audience,
        )
        token = self._signer.sign(claims)
        expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=UTC)
        log.info("realtime.token_issued", expires_at=expires_at.isoformat())
        return IssuedRealtimeToken(token=token, expires_at=expires_at)
