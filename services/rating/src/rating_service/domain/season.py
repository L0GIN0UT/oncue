"""Сезон ladder-а (ADR 0039): жизненный цикл и события."""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from gv_core.domain import AggregateRoot
from rating_service.domain.errors import SeasonNotActiveError
from rating_service.domain.events import (
    SeasonFinished,
    SeasonStandingsFinalized,
    SeasonStarted,
    StandingEntry,
)
from rating_service.domain.ladder import LadderConfig


class SeasonStatus(StrEnum):
    """Состояние сезона."""

    SCHEDULED = "scheduled"
    ACTIVE = "active"
    FINISHED = "finished"


class ClosedBy(StrEnum):
    """Кто закрыл сезон (контракт ``SeasonFinished.closed_by``)."""

    SCHEDULE = "schedule"
    ADMIN = "admin"


def standings_ref(ladder: str, season_id: UUID) -> str:
    """Путь к итоговой таблице сезона в API rating."""
    return f"/v1/ladders/{ladder}/leaderboard?season={season_id}"


def rank_standings(entries: Iterable[tuple[UUID, int]], *, top_size: int) -> list[tuple[UUID, int]]:
    """Верх таблицы: рейтинг по убыванию, при равенстве — ``user_id`` по убыванию.

    Тай-брейк совпадает с лидербордом: Redis ``ZREVRANGE`` при равной оценке отдаёт члены в
    обратном лексикографическом порядке, а членом служит ``str(user_id)``.

    Args:
        entries: ``(user_id, rating)`` игроков лидерборда в любом порядке.
        top_size: сколько мест взять.

    Returns:
        ``(user_id, rating)`` по местам.
    """
    return heapq.nlargest(top_size, entries, key=lambda e: (e[1], str(e[0])))


class Season(AggregateRoot[UUID]):
    """Сезон: плановый → действующий → завершённый."""

    def __init__(
        self,
        *,
        season_id: UUID,
        ladder: str,
        number: int,
        ladder_version: int,
        starts_at: datetime,
        ends_at: datetime,
        status: SeasonStatus,
        finished_at: datetime | None = None,
        closed_by: ClosedBy | None = None,
        players_count: int | None = None,
    ) -> None:
        super().__init__(season_id)
        self.season_id = season_id
        self.ladder = ladder
        self.number = number
        self.ladder_version = ladder_version
        self.starts_at = starts_at
        self.ends_at = ends_at
        self.status = status
        self.finished_at = finished_at
        self.closed_by = closed_by
        self.players_count = players_count

    @classmethod
    def plan(
        cls,
        season_id: UUID,
        config: LadderConfig,
        *,
        ladder_version: int,
        number: int,
        starts_at: datetime,
    ) -> Season:
        """Новый сезон: длина — из конфига ladder-а; статус ``scheduled`` до ``activate``."""
        return cls(
            season_id=season_id,
            ladder=config.key,
            number=number,
            ladder_version=ladder_version,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(days=config.season.length_days),
            status=SeasonStatus.SCHEDULED,
        )

    def move_start(self, starts_at: datetime, config: LadderConfig) -> None:
        """Перенести начало (ранний старт планового сезона): конец — по длине из конфига."""
        self.starts_at = starts_at
        self.ends_at = starts_at + timedelta(days=config.season.length_days)

    def activate(self, config: LadderConfig, *, reset_applied: bool) -> None:
        """Сезон начался: ``SeasonStarted`` (с мягким сбросом, если он применён к рейтингам)."""
        self.status = SeasonStatus.ACTIVE
        reset = config.season.soft_reset
        self.record(
            SeasonStarted(
                aggregate_id=str(self.season_id),
                season_id=self.season_id,
                ladder=self.ladder,
                number=self.number,
                ladder_version=self.ladder_version,
                starts_at=self.starts_at,
                ends_at=self.ends_at,
                soft_reset={"target": reset.target, "ratio": reset.ratio}
                if reset_applied
                else None,
            )
        )

    def is_due(self, now: datetime) -> bool:
        """Время сезона вышло."""
        return self.status is SeasonStatus.ACTIVE and self.ends_at <= now

    def overlaps(self, starts_at: datetime, ends_at: datetime) -> bool:
        """Пересекается ли сезон с периодом ``[starts_at, ends_at)``."""
        return self.starts_at < ends_at and starts_at < self.ends_at

    def finish(self, now: datetime, closed_by: ClosedBy, players_count: int) -> None:
        """Закрыть сезон и зафиксировать ``SeasonFinished``.

        Raises:
            SeasonNotActiveError: сезон не действует.
        """
        if self.status is not SeasonStatus.ACTIVE:
            raise SeasonNotActiveError("закрыть можно только действующий сезон")
        self.status = SeasonStatus.FINISHED
        self.finished_at = now
        self.closed_by = closed_by
        self.players_count = players_count
        self.record(
            SeasonFinished(
                aggregate_id=str(self.season_id),
                season_id=self.season_id,
                ladder=self.ladder,
                number=self.number,
                finished_at=now,
                closed_by=closed_by.value,
                players_count=players_count,
                standings_ref=standings_ref(self.ladder, self.season_id),
            )
        )

    def record_standings(self, ranked: Sequence[tuple[UUID, int]], *, chunk_size: int) -> None:
        """Зафиксировать верх итоговой таблицы пачками ``SeasonStandingsFinalized``.

        Args:
            ranked: ``(user_id, rating)`` по местам; пусто — события нет.
            chunk_size: максимум мест в пачке.

        Raises:
            SeasonNotActiveError: сезон ещё не закрыт.
        """
        if self.status is not SeasonStatus.FINISHED or self.finished_at is None:
            raise SeasonNotActiveError("итоги публикуются только по закрытому сезону")
        chunks = [ranked[i : i + chunk_size] for i in range(0, len(ranked), chunk_size)]
        for index, part in enumerate(chunks):
            first = index * chunk_size
            self.record(
                SeasonStandingsFinalized(
                    aggregate_id=f"{self.season_id}:{index}",
                    season_id=self.season_id,
                    ladder=self.ladder,
                    number=self.number,
                    finished_at=self.finished_at,
                    chunk=index,
                    chunks_total=len(chunks),
                    entries=tuple(
                        StandingEntry(user_id=user_id, place=first + offset, rating=rating)
                        for offset, (user_id, rating) in enumerate(part, start=1)
                    ),
                )
            )
