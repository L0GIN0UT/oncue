"""Отписка от рассылки по подписанной ссылке (единственная мутация без входа).

Безопасность (план этапа 5, §9.8): токен проверяется HMAC за постоянное время; чужой, поддельный
и повреждённый токен дают одну и ту же ошибку; на невалидный токен выполняется то же обращение
к БД, что и на валидный (время ответа не раскрывает существование пользователя); попытки по IP
ограничены, недоступный лимитер закрывает ручку (fail-closed).
"""

from __future__ import annotations

from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import RateLimitedError
from notifications_service.application.ports import AttemptLimiter, UnitOfWorkFactory
from notifications_service.domain.errors import UnsubscribeInvalidError
from notifications_service.domain.tokens import UnsubscribeTokens

log = structlog.get_logger(__name__)

# Пользователя с таким id нет: обращение к БД остаётся, эффекта нет.
_NOBODY = UUID(int=0)


class Unsubscribe:
    """Отзывает согласие на рассылку по токену из письма."""

    def __init__(
        self,
        tokens: UnsubscribeTokens,
        limiter: AttemptLimiter,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        *,
        max_attempts: int,
        window_s: int,
    ) -> None:
        self._tokens = tokens
        self._limiter = limiter
        self._uow = uow_factory
        self._clock = clock
        self._max_attempts = max_attempts
        self._window_s = window_s

    async def execute(self, token: str, *, client_ip: str) -> None:
        """Отписать владельца токена (повтор валидного токена - то же успешное завершение).

        Raises:
            DependencyUnavailableError: лимитер недоступен (503).
            RateLimitedError: слишком много попыток с IP (429, ``details.retry_after``).
            UnsubscribeInvalidError: токен негоден.
        """
        if await self._limiter.hit(client_ip) > self._max_attempts:
            raise RateLimitedError(
                "слишком много попыток отписки",
                code="rate.limited",
                details={"retry_after": self._window_s},
            )
        parsed = self._tokens.verify(token)
        user_id = parsed[0] if parsed is not None else _NOBODY
        async with self._uow() as uow:
            await uow.subscriptions.unsubscribe(user_id, self._clock.now())
            await uow.commit()
        if parsed is None:
            log.info("notifications.unsubscribe_rejected")
            raise UnsubscribeInvalidError
