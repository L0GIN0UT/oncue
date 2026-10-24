"""Порты санкций (ADR 0052): шов потоков F3 (реализация) и F4 (потребитель).

Реализацию пишет поток F3 (``application/sanctions.py`` и SQL-адаптер); F4 (кейсы модерации,
комментарии) работает только через эти протоколы и в тестах подставляет фейки.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

SanctionKind = Literal["ranked", "chat", "online", "account"]


@dataclass(frozen=True, slots=True)
class SanctionRecord:
    """Запись истории санкций."""

    id: UUID
    user_id: UUID
    kind: SanctionKind
    started_at: datetime
    expires_at: datetime | None
    reason: str
    actor_id: UUID
    revoked_at: datetime | None = None
    revoked_by: UUID | None = None
    revoke_reason: str | None = None


@dataclass(frozen=True, slots=True)
class GrantRequest:
    """Выдача санкции модератором."""

    actor_id: UUID
    actor_perms: frozenset[str]
    user_id: UUID
    kind: SanctionKind
    duration_s: int | None
    reason: str


class SanctionReader(Protocol):
    """Чтение действующих санкций игрока (проверки в social)."""

    async def active_mask(self, user_id: UUID) -> int:
        """Битовая маска действующих санкций (как claim ``sanctions``, ``gv_auth.Sanction``).

        ``chat`` даёт ``CHAT_MUTE | COMMENTS``; ``account`` в маску не входит.
        """
        ...


class SanctionGranter(Protocol):
    """Выдача санкций в текущей транзакции (кейс модерации решается атомарно с санкцией)."""

    async def grant(self, request: GrantRequest) -> SanctionRecord:
        """Выдать санкцию; публикует ``UserSanctionsChanged`` в outbox той же транзакции.

        Raises:
            ForbiddenError: ``social.self_sanction_forbidden`` (себе),
                ``social.account_sanction_forbidden`` (``account`` без права).
            InvalidInputError: срок вне пределов конфига.
        """
        ...
