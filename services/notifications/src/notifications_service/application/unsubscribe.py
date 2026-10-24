"""Отписка от рассылки по токену из письма (единственная мутация без входа).

Безопасность (план этапа 5, §9.8): токен непрозрачный, в БД ищется по sha256; неизвестный,
истёкший и повреждённый токен дают одну и ту же ошибку; на негодный токен выполняется то же число
обращений к БД, что и на годный (время ответа не раскрывает существование токена); попытки по IP
ограничены ДО разбора токена (мусор тоже считается), недоступный лимитер закрывает ручку
(fail-closed).
"""

from __future__ import annotations

from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import RateLimitedError
from notifications_service.application.ports import AttemptLimiter, UnitOfWorkFactory
from notifications_service.domain.errors import UnsubscribeInvalidError
from notifications_service.domain.tokens import hash_token

log = structlog.get_logger(__name__)

# Пользователя с таким id нет: обращение к БД остаётся, эффекта нет.
_NOBODY = UUID(int=0)


class Unsubscribe:
    """Отзывает согласие на рассылку по токену из письма."""

    def __init__(
        self,
        limiter: AttemptLimiter,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        *,
        max_attempts: int,
        window_s: int,
    ) -> None:
        self._limiter = limiter
        self._uow = uow_factory
        self._clock = clock
        self._max_attempts = max_attempts
        self._window_s = window_s

    async def execute(self, token: str, *, client_ip: str) -> None:
        """Отписать владельца токена (повтор годного токена - то же успешное завершение).

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
        now = self._clock.now()
        async with self._uow() as uow:
            owner = await uow.broadcasts.token_owner(hash_token(token), now)
            await uow.subscriptions.unsubscribe(owner or _NOBODY, now)
            await uow.commit()
        if owner is None:
            log.info("notifications.unsubscribe_rejected")
            raise UnsubscribeInvalidError
