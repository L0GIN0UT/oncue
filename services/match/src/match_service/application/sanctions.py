"""Санкции игрока при решениях match (ADR 0052): эффективная маска и проверка ONLINE.

Эффективная маска - проекция ``social.UserSanctionsChanged``, если запись есть, иначе claim
(``domain.sanctions.effective_sanctions``). Недоступная БД проекции не блокирует игру:
действует claim токена (деградация ≤ access TTL), сбой пишется в лог.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import structlog

from gv_core.clock import Clock
from match_service.domain import errors
from match_service.domain.errors import MatchForbiddenError
from match_service.domain.sanctions import ONLINE_BAN, SanctionsSnapshot, effective_sanctions

log = structlog.get_logger(__name__)


class SanctionsProjection(Protocol):
    """Чтение проекции санкций (таблица ``sanctions_proj``)."""

    async def get(self, user_id: UUID) -> SanctionsSnapshot | None:
        """Снимок игрока или ``None`` - событий о нём не было."""
        ...


class SanctionGate:
    """Эффективная маска санкций и проверки доступа к онлайну."""

    def __init__(self, projection: SanctionsProjection, clock: Clock) -> None:
        self._projection = projection
        self._clock = clock

    async def mask(self, user_id: UUID, claim: int) -> int:
        """Маска для решения: проекция, если есть, иначе ``claim``."""
        try:
            snapshot = await self._projection.get(user_id)
        except Exception:
            log.warning("match.sanctions_projection_unavailable", exc_info=True)
            return claim
        return effective_sanctions(snapshot, claim, self._clock.now())

    async def require_online(self, user_id: UUID, claim: int) -> None:
        """Пропустить игрока без санкции ONLINE.

        Raises:
            MatchForbiddenError: ``match.online_banned``.
        """
        if await self.mask(user_id, claim) & ONLINE_BAN:
            raise online_banned()


def online_banned() -> MatchForbiddenError:
    """403 ``match.online_banned``: комнаты, лобби, очереди и реванш закрыты."""
    return MatchForbiddenError("онлайн-игры вам недоступны", code=errors.ONLINE_BANNED)
