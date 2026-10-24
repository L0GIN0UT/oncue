"""Пакетная выдача подтверждённых адресов другим сервисам (рассылки notifications)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

import structlog

from identity_service.application.ports import StrictRateLimiter, UnitOfWorkFactory
from identity_service.application.security import ensure_allowed, rate_key

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class UserEmailsLimit:
    """Лимиты на клиента (``GV_USERS_EMAILS_LIMIT__*``).

    Attributes:
        per_client: Вызовов на ``client_id`` за окно ``window_s``.
        window_s: Окно лимита вызовов, секунд.
        ids_per_hour: Запрошенных идентификаторов на ``client_id`` за час.
    """

    per_client: int = 60
    window_s: int = 60
    ids_per_hour: int = 20000


class ListVerifiedEmails:
    """Адреса пользователей с подтверждённым e-mail и действующим аккаунтом.

    Единственная ручка, массово отдающая чужие адреса: ответ не различает «нет такого»,
    «не подтверждён» и «удалён»; адреса не попадают в логи — только числа, ``client_id`` и
    ``jti`` токена. Лимиты **fail-closed**: при недоступном хранилище счётчиков ручка
    отвечает 503, а не отдаёт адреса без ограничений. Лимиты проверяются до SQL.
    """

    def __init__(
        self, uow: UnitOfWorkFactory, limiter: StrictRateLimiter, limit: UserEmailsLimit
    ) -> None:
        self._uow = uow
        self._limiter = limiter
        self._limit = limit

    async def execute(
        self, client_id: str, token_id: str | None, user_ids: Iterable[UUID]
    ) -> list[tuple[UUID, str]]:
        """Вернуть пары (user_id, e-mail); порядок не гарантируется, дубли схлопываются.

        Raises:
            RateLimitedError: исчерпан лимит вызовов или идентификаторов клиента (429).
            DependencyUnavailableError: лимитер недоступен (503).
        """
        ids = set(user_ids)
        ensure_allowed(
            await self._limiter.hit(
                rate_key("users_emails", client_id),
                limit=self._limit.per_client,
                window_s=self._limit.window_s,
            )
        )
        ensure_allowed(
            await self._limiter.hit(
                rate_key("users_emails_ids", client_id),
                limit=self._limit.ids_per_hour,
                window_s=3600,
                amount=len(ids),
            )
        )
        async with self._uow() as uow:
            found = await uow.users.verified_emails(ids)
        log.info(
            "users.emails_read",
            client_id=client_id,
            token_id=token_id,
            requested=len(ids),
            returned=len(found),
        )
        return found
