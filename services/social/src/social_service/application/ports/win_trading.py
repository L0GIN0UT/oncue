"""Порты детектора win-trading (ADR 0068)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from social_service.domain.win_trading import MatchFact, Signal


class WinTradingStore(Protocol):
    """Факты дуэлей и сигналы (в транзакции потребителя события или чтения)."""

    async def add_fact(self, fact: MatchFact) -> bool:
        """Сохранить факт; ``False`` - матч уже учтён."""
        ...

    async def pair_facts(self, low: UUID, high: UUID, since: datetime) -> Sequence[MatchFact]:
        """Матчи пары не раньше ``since``, новые первыми (позднее пришедшие тоже учитываются)."""
        ...

    async def forfeit_facts(self, user_id: UUID, since: datetime) -> Sequence[MatchFact]:
        """Матчи, где игрок сдался, не раньше ``since``, новые первыми."""
        ...

    async def sync_signal(self, key: str, signal: Signal | None, at: datetime) -> None:
        """Записать сигнал (upsert) или удалить (``None``) по ключу."""
        ...

    async def signals(
        self,
        *,
        kind: str | None,
        severity: str | None,
        user_id: UUID | None,
        after: tuple[datetime, str] | None,
        limit: int,
    ) -> Sequence[Signal]:
        """Сигналы очереди: свежие первыми; ``user_id`` - игрок на любой стороне."""
        ...

    async def delete_user(self, user_id: UUID) -> None:
        """Удалить факты и сигналы игрока (удаление аккаунта)."""
        ...
