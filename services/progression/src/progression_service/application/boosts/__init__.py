"""Бусты опыта: реальный ``RewardHook`` и состояние для профиля (ADR 0044)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from progression_service.application.ports import ProgressionUnitOfWork
from progression_service.domain import boosts


@dataclass(frozen=True, slots=True)
class BoostState:
    """Действующий буст игрока и число бустов в очереди."""

    multiplier: Decimal | None
    matches_left: int
    queued: int


class BoostRewardHook:
    """Множитель опыта онлайн-матча из бустов игрока (реализация ``RewardHook``).

    Работает в транзакции матча на её UoW и под блокировками ``FOR UPDATE`` игроков: внешнего
    I/O нет. Матч тратит ровно один матч действующего буста; повтор того же ``match_id``
    возвращает уже применённый множитель и ничего не тратит. Решает, нужен ли множитель
    (опыт матча больше нуля), ядро начисления: хук зовут только в этом случае.
    """

    async def online_xp_multiplier(
        self, uow: ProgressionUnitOfWork, user_id: UUID, match_id: UUID, now: datetime
    ) -> Decimal:
        """Множитель опыта игрока в матче; ``1`` - буста нет."""
        applied = await uow.boosts.spend_of(user_id, match_id)
        if applied is not None:
            return applied
        current = boosts.active(await uow.boosts.live(user_id, lock=True))
        if current is None:
            return Decimal(1)
        # Трата матча (PK user+match) в той же транзакции: откат матча возвращает буст.
        await uow.boosts.spend(current, match_id, now)
        return current.multiplier


async def boost_state(uow: ProgressionUnitOfWork, user_id: UUID) -> BoostState:
    """Действующий буст и очередь игрока (для ``me/progress``)."""
    live = await uow.boosts.live(user_id)
    current = boosts.active(live)
    return BoostState(
        multiplier=None if current is None else current.multiplier,
        matches_left=0 if current is None else current.matches_left,
        queued=boosts.queued(live),
    )
