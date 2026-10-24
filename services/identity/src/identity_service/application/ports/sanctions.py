"""Порты санкций identity (ADR 0052)."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from identity_service.domain.user_sanctions import UserSanctions


class UserSanctionRepository(Protocol):
    """Снимки санкций игроков (проекция ``social.UserSanctionsChanged``)."""

    async def get(self, user_id: UUID) -> UserSanctions | None:
        """Снимок игрока или ``None`` - санкций не было."""
        ...

    async def apply(self, snapshot: UserSanctions) -> bool:
        """Сохранить, если ``version`` строго новее сохранённой; ``False`` - событие устарело."""
        ...
