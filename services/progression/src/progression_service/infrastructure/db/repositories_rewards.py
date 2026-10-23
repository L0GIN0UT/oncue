"""Postgres: репозитории заданий, метрик, бейджей, колеса, бустов и журнала выдач (поток P2)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gv_core.ids import uuid7
from progression_service.application.ports.rewards import AssignedQuest, SpinRecord
from progression_service.domain.boosts import Boost
from progression_service.domain.config import PrizeDef, PrizeKind, Reward
from progression_service.domain.quests import QuestSnapshot
from progression_service.infrastructure.db.tables_rewards import (
    badges_unlocked,
    boost_spends,
    boosts,
    grants_log,
    metric_counters,
    metric_marks,
    quest_assignments,
    quest_progress,
    wheel_spins,
)


class SqlQuests:
    """Назначения заданий и прогресс."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @staticmethod
    def _quest(r: Any) -> AssignedQuest:
        return AssignedQuest(
            snapshot=QuestSnapshot(
                key=r.quest_key,
                period=r.period,
                period_start=r.period_start,
                title=r.title,
                description=r.description,
                metric=r.metric,
                target=r.target,
                domain=r.domain,
                reward=Reward(xp=int(r.reward_xp), verses=int(r.reward_verses)),
                config_version=r.config_version,
            ),
            progress=int(r.progress),
            completed_at=r.completed_at,
        )

    async def assigned(
        self, user_id: UUID, period: str, start: date | None, *, lock: bool = False
    ) -> list[AssignedQuest]:
        """Задания периода игрока (``lock`` - строки прогресса ``FOR UPDATE``)."""
        a, p = quest_assignments, quest_progress
        stmt = (
            select(
                a.c.quest_key,
                a.c.period,
                a.c.period_start,
                a.c.title,
                a.c.description,
                a.c.metric,
                a.c.target,
                a.c.domain,
                a.c.reward_xp,
                a.c.reward_verses,
                a.c.config_version,
                p.c.progress,
                p.c.completed_at,
            )
            .select_from(
                a.join(
                    p,
                    (p.c.user_id == a.c.user_id)
                    & (p.c.quest_key == a.c.quest_key)
                    & (p.c.period_start == a.c.period_start),
                )
            )
            .where(a.c.user_id == user_id, a.c.period == period)
            .order_by(a.c.assigned_at, a.c.quest_key)
        )
        if start is not None:
            stmt = stmt.where(a.c.period_start == start)
        if lock:
            stmt = stmt.with_for_update(of=p)
        return [self._quest(r) for r in await self._s.execute(stmt)]

    async def add(self, user_id: UUID, snapshots: list[QuestSnapshot], now: datetime) -> None:
        """Записать назначения и нулевой прогресс; существующие не меняются."""
        for snap in snapshots:
            created = (
                await self._s.execute(
                    pg_insert(quest_assignments)
                    .values(
                        user_id=user_id,
                        quest_key=snap.key,
                        period_start=snap.period_start,
                        period=snap.period,
                        title=snap.title,
                        description=snap.description,
                        metric=snap.metric,
                        target=snap.target,
                        domain=snap.domain,
                        reward_xp=snap.reward.xp,
                        reward_verses=snap.reward.verses,
                        config_version=snap.config_version,
                        assigned_at=now,
                    )
                    .on_conflict_do_nothing()
                    .returning(quest_assignments.c.quest_key)
                )
            ).first()
            if created is not None:
                await self._s.execute(
                    pg_insert(quest_progress)
                    .values(
                        user_id=user_id,
                        quest_key=snap.key,
                        period_start=snap.period_start,
                        progress=0,
                        updated_at=now,
                    )
                    .on_conflict_do_nothing()
                )

    async def save_progress(  # noqa: PLR0917 - ключ записи + значения
        self,
        user_id: UUID,
        key: str,
        start: date,
        progress: int,
        completed_at: datetime | None,
        now: datetime,
    ) -> None:
        """Записать прогресс и момент выполнения."""
        await self._s.execute(
            update(quest_progress)
            .where(
                quest_progress.c.user_id == user_id,
                quest_progress.c.quest_key == key,
                quest_progress.c.period_start == start,
            )
            .values(progress=progress, completed_at=completed_at, updated_at=now)
        )


class SqlMetrics:
    """Метрики игрока и отметки предметов событий."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def mark(self, subject_key: str, user_id: UUID, now: datetime) -> bool:
        """Отметить предмет; ``False`` - уже учтён."""
        row = (
            await self._s.execute(
                pg_insert(metric_marks)
                .values(subject_key=subject_key, user_id=user_id, at=now)
                .on_conflict_do_nothing(index_elements=[metric_marks.c.subject_key])
                .returning(metric_marks.c.subject_key)
            )
        ).first()
        return row is not None

    async def values(self, user_id: UUID) -> dict[str, int]:
        """Значения метрик игрока."""
        rows = await self._s.execute(
            select(metric_counters.c.metric, metric_counters.c.value).where(
                metric_counters.c.user_id == user_id
            )
        )
        return {r.metric: int(r.value) for r in rows}

    async def save(self, user_id: UUID, metric: str, value: int, now: datetime) -> None:
        """Записать значение метрики."""
        values = {"value": value, "updated_at": now}
        await self._s.execute(
            pg_insert(metric_counters)
            .values(user_id=user_id, metric=metric, **values)
            .on_conflict_do_update(
                index_elements=[metric_counters.c.user_id, metric_counters.c.metric], set_=values
            )
        )


class SqlBadges:
    """Полученные бейджи."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def unlocked(self, user_id: UUID) -> dict[str, datetime]:
        """Ключи полученных бейджей и время получения."""
        rows = await self._s.execute(
            select(badges_unlocked.c.badge_key, badges_unlocked.c.unlocked_at).where(
                badges_unlocked.c.user_id == user_id
            )
        )
        return {r.badge_key: r.unlocked_at for r in rows}

    async def add(self, user_id: UUID, key: str, version: int, now: datetime) -> bool:
        """Выдать бейдж; ``False`` - уже выдан."""
        row = (
            await self._s.execute(
                pg_insert(badges_unlocked)
                .values(user_id=user_id, badge_key=key, config_version=version, unlocked_at=now)
                .on_conflict_do_nothing()
                .returning(badges_unlocked.c.badge_key)
            )
        ).first()
        return row is not None


class SqlSpins:
    """Спины колеса."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, user_id: UUID, day: date) -> SpinRecord | None:
        """Спин игрока за сутки."""
        r = (
            await self._s.execute(
                select(wheel_spins).where(
                    wheel_spins.c.user_id == user_id, wheel_spins.c.day == day
                )
            )
        ).first()
        if r is None:
            return None
        return SpinRecord(
            user_id=r.user_id,
            day=r.day,
            sector_key=r.sector_key,
            prize=PrizeDef(
                kind=PrizeKind(r.prize_kind),
                amount=int(r.prize_amount),
                multiplier=None if r.prize_multiplier is None else Decimal(r.prize_multiplier),
                matches=r.prize_matches,
                item_key=r.prize_item_key,
            ),
            config_version=r.config_version,
            spun_at=r.spun_at,
        )

    async def add(self, record: SpinRecord) -> bool:
        """``INSERT … ON CONFLICT DO NOTHING``; ``False`` - за сутки уже есть запись."""
        prize = record.prize
        row = (
            await self._s.execute(
                pg_insert(wheel_spins)
                .values(
                    user_id=record.user_id,
                    day=record.day,
                    sector_key=record.sector_key,
                    prize_kind=prize.kind.value,
                    prize_amount=prize.amount,
                    prize_multiplier=prize.multiplier,
                    prize_matches=prize.matches,
                    prize_item_key=prize.item_key,
                    config_version=record.config_version,
                    spun_at=record.spun_at,
                )
                .on_conflict_do_nothing(index_elements=[wheel_spins.c.user_id, wheel_spins.c.day])
                .returning(wheel_spins.c.user_id)
            )
        ).first()
        return row is not None


class SqlBoosts:
    """Бусты опыта и их траты."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def live(self, user_id: UUID, *, lock: bool = False) -> list[Boost]:
        """Бусты с остатком в порядке очереди."""
        stmt = (
            select(boosts)
            .where(boosts.c.user_id == user_id, boosts.c.matches_left > 0)
            .order_by(boosts.c.queued_at, boosts.c.id)
        )
        if lock:
            stmt = stmt.with_for_update()
        return [
            Boost(
                id=r.id,
                user_id=r.user_id,
                multiplier=Decimal(r.multiplier),
                matches_left=r.matches_left,
                queued_at=r.queued_at,
            )
            for r in await self._s.execute(stmt)
        ]

    async def add(self, boost: Boost, *, source: str, source_ref: str) -> bool:
        """Добавить буст; ``False`` - буст этого источника уже есть."""
        row = (
            await self._s.execute(
                pg_insert(boosts)
                .values(
                    id=boost.id,
                    user_id=boost.user_id,
                    multiplier=boost.multiplier,
                    matches_left=boost.matches_left,
                    source=source,
                    source_ref=source_ref,
                    queued_at=boost.queued_at,
                )
                .on_conflict_do_nothing(index_elements=[boosts.c.user_id, boosts.c.source_ref])
                .returning(boosts.c.id)
            )
        ).first()
        return row is not None

    async def spend_of(self, user_id: UUID, match_id: UUID) -> Decimal | None:
        """Множитель, уже применённый к матчу."""
        value = await self._s.scalar(
            select(boost_spends.c.multiplier).where(
                boost_spends.c.user_id == user_id, boost_spends.c.match_id == match_id
            )
        )
        return None if value is None else Decimal(value)

    async def spend(self, boost: Boost, match_id: UUID, now: datetime) -> bool:
        """Записать трату матча и уменьшить остаток; ``False`` - трата матча уже есть."""
        row = (
            await self._s.execute(
                pg_insert(boost_spends)
                .values(
                    user_id=boost.user_id,
                    match_id=match_id,
                    boost_id=boost.id,
                    multiplier=boost.multiplier,
                    at=now,
                )
                .on_conflict_do_nothing()
                .returning(boost_spends.c.match_id)
            )
        ).first()
        if row is None:
            return False
        await self._s.execute(
            update(boosts)
            .where(boosts.c.id == boost.id)
            .values(matches_left=boosts.c.matches_left - 1)
        )
        return True


class SqlGrants:
    """Журнал выдач предметов (заглушка 5a)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(
        self, user_id: UUID, source: str, source_ref: str, item_key: str, now: datetime
    ) -> bool:
        """Записать выдачу; ``False`` - запись источника уже есть."""
        row = (
            await self._s.execute(
                pg_insert(grants_log)
                .values(
                    id=uuid7(),
                    user_id=user_id,
                    source=source,
                    source_ref=source_ref,
                    item_key=item_key,
                    at=now,
                )
                .on_conflict_do_nothing()
                .returning(grants_log.c.id)
            )
        ).first()
        return row is not None
