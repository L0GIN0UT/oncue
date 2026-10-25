"""Постраничный список активных пользователей для других сервисов (аудитория notifications).

Отдаёт только идентификаторы (без e-mail и никнеймов): сервис-потребитель сам решает, что с ними
делать. Лимиты **fail-closed**, как у выдачи адресов: при недоступном хранилище счётчиков ручка
отвечает 503. Лимит проверяется до SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog

from identity_service.application.ports import StrictRateLimiter, UnitOfWorkFactory
from identity_service.application.security import ensure_allowed, rate_key

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ActiveUsersLimit:
    """Настройки списка (``GV_USERS_ACTIVE__*``).

    Attributes:
        max_page: Предел пачки; больший запрошенный ``limit`` усекается.
        per_client: Вызовов на ``client_id`` за окно ``window_s``.
        window_s: Окно лимита вызовов, секунд.
    """

    max_page: int = 1000
    per_client: int = 120
    window_s: int = 60


@dataclass(frozen=True, slots=True)
class ActiveUsersPage:
    """Страница: идентификаторы по возрастанию и курсор следующей (``None`` - конец)."""

    ids: list[UUID]
    next_cursor: UUID | None


class ListActiveUsers:
    """Активные (не удалённые) пользователи, keyset по ``id``."""

    def __init__(
        self, uow: UnitOfWorkFactory, limiter: StrictRateLimiter, limit: ActiveUsersLimit
    ) -> None:
        self._uow = uow
        self._limiter = limiter
        self._limit = limit

    async def execute(
        self, client_id: str, token_id: str | None, *, after: UUID | None, limit: int
    ) -> ActiveUsersPage:
        """Следующая страница после ``after``.

        Raises:
            RateLimitedError: исчерпан лимит вызовов клиента (429).
            DependencyUnavailableError: лимитер недоступен (503).
        """
        ensure_allowed(
            await self._limiter.hit(
                rate_key("users_active", client_id),
                limit=self._limit.per_client,
                window_s=self._limit.window_s,
            )
        )
        size = max(1, min(limit, self._limit.max_page))
        async with self._uow() as uow:
            ids = await uow.users.active_ids_after(after, size)
        log.info("users.active_listed", client_id=client_id, token_id=token_id, returned=len(ids))
        return ActiveUsersPage(ids, ids[-1] if len(ids) == size else None)
