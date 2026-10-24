"""Санкции игрока при решениях match (ADR 0052): эффективная маска и проверка ONLINE.

Эффективная маска - проекция ``social.UserSanctionsChanged``, если запись есть, иначе claim
(``domain.sanctions.effective_sanctions``). Недоступная БД проекции не блокирует игру:
действует claim токена (деградация ≤ access TTL), сбой пишется в лог.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

import structlog

from gv_core.clock import Clock
from match_service.domain import errors
from match_service.domain.errors import MatchForbiddenError, MatchUnavailableError
from match_service.domain.sanctions import ONLINE_BAN, SanctionsSnapshot, effective_sanctions

log = structlog.get_logger(__name__)


class SanctionsProjection(Protocol):
    """Чтение проекции санкций (таблица ``sanctions_proj``)."""

    async def get(self, user_id: UUID) -> SanctionsSnapshot | None:
        """Снимок игрока или ``None`` - событий о нём не было."""
        ...


class SanctionGate:
    """Эффективная маска санкций и проверки доступа к онлайну."""

    def __init__(
        self,
        projection: SanctionsProjection,
        clock: Clock,
        on_failure: Callable[[], None] = lambda: None,
    ) -> None:
        self._projection = projection
        self._clock = clock
        self._on_failure = on_failure

    async def mask(self, user_id: UUID, claim: int, *, strict: bool = False) -> int:
        """Маска для решения: проекция, если есть, иначе ``claim``.

        Недоступная проекция: ``strict`` (входы в онлайн) - отказ, иначе (игрок уже в комнате)
        деградация до ``claim``; оба случая считаются метрикой сбоев.

        Raises:
            MatchUnavailableError: ``match.sanctions_unavailable`` при ``strict``.
        """
        try:
            snapshot = await self._projection.get(user_id)
        except Exception as exc:
            self._on_failure()
            log.warning("match.sanctions_projection_unavailable", strict=strict, exc_info=True)
            if strict:
                raise MatchUnavailableError(
                    "проверка санкций недоступна",
                    code=errors.SANCTIONS_UNAVAILABLE,
                    details={"retry_after": 5},
                ) from exc
            return claim
        return effective_sanctions(snapshot, claim, self._clock.now())

    async def require_online(self, user_id: UUID, claim: int, *, strict: bool = True) -> None:
        """Пропустить игрока без санкции ONLINE.

        Raises:
            MatchForbiddenError: ``match.online_banned``.
            MatchUnavailableError: ``match.sanctions_unavailable`` при ``strict``.
        """
        if await self.mask(user_id, claim, strict=strict) & ONLINE_BAN:
            raise online_banned()


def online_banned() -> MatchForbiddenError:
    """403 ``match.online_banned``: комнаты, лобби, очереди и реванш закрыты."""
    return MatchForbiddenError("онлайн-игры вам недоступны", code=errors.ONLINE_BANNED)
