"""Порты потока P2: задания, метрики, бейджи, колесо, бусты, журнал выдач (ADR 0042, 0044)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from progression_service.domain.boosts import Boost
from progression_service.domain.config import PrizeDef
from progression_service.domain.quests import QuestSnapshot


@dataclass(frozen=True, slots=True)
class AssignedQuest:
    """Назначенное задание: снимок, прогресс и момент выполнения."""

    snapshot: QuestSnapshot
    progress: int
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class SpinRecord:
    """Спин колеса за сутки (запись ``wheel_spins``)."""

    user_id: UUID
    day: date
    sector_key: str
    prize: PrizeDef
    config_version: int
    spun_at: datetime


class QuestRepository(Protocol):
    """Назначения заданий и прогресс."""

    async def assigned(
        self, user_id: UUID, period: str, start: date | None, *, lock: bool = False
    ) -> list[AssignedQuest]:
        """Задания игрока периода (``start=None`` - все с этим периодом, для разовых).

        ``lock=True`` берёт строки прогресса ``FOR UPDATE``.
        """
        ...

    async def add(self, user_id: UUID, snapshots: list[QuestSnapshot], now: datetime) -> None:
        """Записать назначения (с нулевым прогрессом); существующие не трогаются."""
        ...

    async def save_progress(  # noqa: PLR0917 - ключ записи + значения
        self,
        user_id: UUID,
        key: str,
        start: date,
        progress: int,
        completed_at: datetime | None,
        now: datetime,
    ) -> None:
        """Записать прогресс задания."""
        ...


class MetricRepository(Protocol):
    """Накопленные метрики игрока и отметки обработанных предметов событий."""

    async def mark(self, subject_key: str, user_id: UUID, now: datetime) -> bool:
        """Отметить предмет; ``False`` - предмет уже учтён."""
        ...

    async def values(self, user_id: UUID) -> dict[str, int]:
        """Значения метрик игрока."""
        ...

    async def save(self, user_id: UUID, metric: str, value: int, now: datetime) -> None:
        """Записать значение метрики."""
        ...


class BadgeRepository(Protocol):
    """Полученные бейджи."""

    async def unlocked(self, user_id: UUID) -> dict[str, datetime]:
        """Ключи полученных бейджей и время получения."""
        ...

    async def add(self, user_id: UUID, key: str, version: int, now: datetime) -> bool:
        """Выдать бейдж; ``False`` - уже выдан."""
        ...


class SpinRepository(Protocol):
    """Спины колеса."""

    async def get(self, user_id: UUID, day: date) -> SpinRecord | None:
        """Спин игрока за сутки."""
        ...

    async def add(self, record: SpinRecord) -> bool:
        """``INSERT … ON CONFLICT DO NOTHING``; ``False`` - за сутки уже есть запись."""
        ...


class BoostRepository(Protocol):
    """Бусты опыта и их траты."""

    async def live(self, user_id: UUID, *, lock: bool = False) -> list[Boost]:
        """Бусты с остатком матчей в порядке очереди (``lock`` - ``FOR UPDATE``)."""
        ...

    async def add(self, boost: Boost, *, source: str, source_ref: str) -> bool:
        """Добавить буст; ``False`` - буст этого источника уже есть."""
        ...

    async def spend_of(self, user_id: UUID, match_id: UUID) -> Decimal | None:
        """Множитель, уже применённый к матчу; ``None`` - буст матчу не тратился."""
        ...

    async def spend(self, boost: Boost, match_id: UUID, now: datetime) -> bool:
        """Потратить один матч буста и записать трату; ``False`` - трата матча уже есть."""
        ...


class GrantRepository(Protocol):
    """Журнал выдач предметов (заглушка 5a)."""

    async def add(
        self, user_id: UUID, source: str, source_ref: str, item_key: str, now: datetime
    ) -> bool:
        """Записать выдачу; ``False`` - запись источника уже есть."""
        ...
