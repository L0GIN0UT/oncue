"""Порты эксплуатационных задач: контрольные точки сверки и ретенция (ADR 0062)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Контрольная точка сверки одного журнала.

    Attributes:
        last_id: Последняя покрытая проводка (uuid7 упорядочен по времени создания).
        last_full_at: Когда был последний полный проход.
        full_mismatches: Расхождения последнего полного прохода (для метрики-алерта).
    """

    last_id: UUID | None
    last_full_at: datetime | None
    full_mismatches: int


class ReconcileCheckpointRepository(Protocol):
    """Контрольные точки (по строке на журнал)."""

    async def load(self) -> dict[str, Checkpoint]:
        """Точки по имени журнала (нет строки - журнал ещё не сверялся)."""
        ...

    async def save(
        self,
        ledger: str,
        *,
        last_id: UUID,
        now: datetime,
        full: bool = False,
        full_mismatches: int = 0,
    ) -> None:
        """Сдвинуть точку; ``full`` - итог полного прохода (время и число расхождений)."""
        ...


class RetentionRepository(Protocol):
    """Удаление устаревших строк пачками (журналы сюда не входят)."""

    async def purge_metric_marks(self, cutoff: datetime, *, limit: int) -> int:
        """Удалить до ``limit`` отметок с ``at < cutoff``; вернуть число удалённых."""
        ...

    async def purge_quest_progress(self, cutoff: date, *, limit: int) -> int:
        """Удалить до ``limit`` строк прогресса дневных/недельных заданий старше ``cutoff``.

        Разовые задания не удаляются никогда: их строка - ключ идемпотентности награды.
        """
        ...
