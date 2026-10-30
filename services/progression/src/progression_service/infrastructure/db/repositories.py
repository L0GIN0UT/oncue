"""Postgres progression: репозитории и Unit of Work с записью событий в outbox.

Два режима UoW: своя транзакция (API, задачи) или SAVEPOINT в транзакции consumer, где уже
стоит отметка inbox (коммит всей транзакции делает ``EventProcessor``).
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from sqlalchemy import func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from gv_core.clock import Clock
from gv_core.domain import DomainEvent
from gv_core.ids import uuid7
from gv_messaging.envelope import EventEnvelope
from gv_messaging.outbox import OutboxWriter
from progression_service.application.ports import (
    DailyCounters,
    LedgerMismatch,
    ProgressRow,
    PublishedConfig,
    SessionLedgerRows,
    XpEntry,
)
from progression_service.domain.config import ProgressionConfig
from progression_service.domain.daily_streak import Streak
from progression_service.domain.ledger import Bucket, Direction, Entry
from progression_service.infrastructure.db.repositories_avatar import SqlAvatars
from progression_service.infrastructure.db.repositories_events import SqlEvents
from progression_service.infrastructure.db.repositories_ops import (
    SqlLedgerRanges,
    SqlReconcileCheckpoints,
    SqlRetention,
)
from progression_service.infrastructure.db.repositories_rewards import (
    SqlBadges,
    SqlBoosts,
    SqlGrants,
    SqlMetrics,
    SqlQuests,
    SqlSpins,
)
from progression_service.infrastructure.db.repositories_shop import (
    SqlEquipped,
    SqlInventory,
    SqlItems,
    SqlPacks,
    SqlPromos,
)
from progression_service.infrastructure.db.tables import (
    TABLES,
    accrual_marks,
    balances,
    config_versions,
    currency_ledger,
    daily_counters,
    daily_streaks,
    progress,
    reward_periods,
    xp_ledger,
)

_MISMATCH_SQL = text(
    """
    SELECT 'currency' AS kind, COALESCE(l.user_id, b.user_id) AS user_id,
           COALESCE(l.bucket, b.bucket) AS bucket,
           COALESCE(l.s, 0) AS ledger_sum, COALESCE(b.amount, 0) AS stored
      FROM (SELECT user_id, bucket,
                   SUM(CASE direction WHEN 'credit' THEN amount ELSE -amount END) AS s
              FROM currency_ledger GROUP BY user_id, bucket) l
      FULL OUTER JOIN balances b ON b.user_id = l.user_id AND b.bucket = l.bucket
     WHERE COALESCE(l.s, 0) <> COALESCE(b.amount, 0)
    UNION ALL
    SELECT 'xp', COALESCE(l.user_id, p.user_id), NULL,
           COALESCE(l.s, 0), COALESCE(p.total_xp, 0)
      FROM (SELECT user_id,
                   SUM(CASE direction WHEN 'credit' THEN amount ELSE -amount END) AS s
              FROM xp_ledger GROUP BY user_id) l
      FULL OUTER JOIN progress p ON p.user_id = l.user_id
     WHERE COALESCE(l.s, 0) <> COALESCE(p.total_xp, 0)
    LIMIT :limit
    """
)


class SqlConfigs:
    """Версии конфига."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def published(self) -> PublishedConfig | None:
        """Действующая версия."""
        row = (
            await self._s.execute(
                select(config_versions.c.version, config_versions.c.body).where(
                    config_versions.c.status == "published"
                )
            )
        ).first()
        return (
            None if row is None else PublishedConfig(row.version, ProgressionConfig.parse(row.body))
        )

    async def effective_at(self, moment: datetime) -> PublishedConfig | None:
        """Версия, действовавшая в ``moment``; раньше первой публикации — самая ранняя."""
        published = config_versions.c.published_at.is_not(None)
        found = None
        for condition, order in (
            (
                config_versions.c.published_at <= moment,
                (config_versions.c.published_at.desc(), config_versions.c.version.desc()),
            ),
            (None, (config_versions.c.published_at.asc(), config_versions.c.version.asc())),
        ):
            stmt = select(config_versions.c.version, config_versions.c.body).where(published)
            if condition is not None:
                stmt = stmt.where(condition)
            found = (await self._s.execute(stmt.order_by(*order).limit(1))).first()
            if found is not None:
                break
        return (
            None
            if found is None
            else PublishedConfig(found.version, ProgressionConfig.parse(found.body))
        )


class SqlProgress:
    """Прогресс игроков."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @staticmethod
    def _row(r: Any) -> ProgressRow:
        return ProgressRow(
            user_id=r.user_id,
            total_xp=r.total_xp,
            level=r.level,
            config_version=r.config_version,
            updated_at=r.updated_at,
            hidden=r.hidden,
        )

    async def lock(self, user_id: UUID, *, config_version: int, now: datetime) -> ProgressRow:
        """Создать строку при отсутствии и взять ``FOR UPDATE``."""
        await self._s.execute(
            pg_insert(progress)
            .values(
                user_id=user_id,
                total_xp=0,
                level=1,
                config_version=config_version,
                updated_at=now,
                hidden=False,
            )
            .on_conflict_do_nothing(index_elements=[progress.c.user_id])
        )
        row = (
            await self._s.execute(
                select(progress).where(progress.c.user_id == user_id).with_for_update()
            )
        ).one()
        return self._row(row)

    async def get(self, user_id: UUID) -> ProgressRow | None:
        """Прогресс без блокировки."""
        row = (await self._s.execute(select(progress).where(progress.c.user_id == user_id))).first()
        return None if row is None else self._row(row)

    async def save(self, row: ProgressRow) -> None:
        """Сохранить опыт, уровень и версию конфига."""
        await self._s.execute(
            update(progress)
            .where(progress.c.user_id == row.user_id)
            .values(
                total_xp=row.total_xp,
                level=row.level,
                config_version=row.config_version,
                updated_at=row.updated_at,
            )
        )


class SqlLedger(SqlLedgerRanges):
    """Журналы опыта и Версов (только добавление и чтение)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add_xp(self, entry: XpEntry) -> bool:
        """Проводка опыта; ``False`` — ``op_id`` уже есть."""
        row = (
            await self._s.execute(
                pg_insert(xp_ledger)
                .values(
                    id=uuid7(),
                    op_id=entry.op_id,
                    user_id=entry.user_id,
                    direction=Direction.CREDIT.value,
                    amount=entry.amount,
                    base_amount=entry.base_amount,
                    boost_multiplier=entry.boost_multiplier,
                    reason=entry.reason,
                    source_ref=entry.source_ref,
                    reversal_of=None,
                    actor_id=None,
                    config_version=entry.config_version,
                    at=entry.at,
                )
                .on_conflict_do_nothing(index_elements=[xp_ledger.c.op_id])
                .returning(xp_ledger.c.id)
            )
        ).first()
        return row is not None

    async def add_currency(self, entry: Entry) -> bool:
        """Проводка Версов; ``False`` — ``op_id`` уже есть."""
        row = (
            await self._s.execute(
                pg_insert(currency_ledger)
                .values(
                    id=uuid7(),
                    op_id=entry.op_id,
                    user_id=entry.user_id,
                    direction=entry.direction.value,
                    amount=entry.amount,
                    bucket=entry.bucket.value,
                    reason=entry.reason,
                    source_ref=entry.source_ref,
                    reversal_of=entry.reversal_of,
                    actor_id=entry.actor_id,
                    config_version=entry.config_version,
                    at=entry.at,
                )
                .on_conflict_do_nothing(index_elements=[currency_ledger.c.op_id])
                .returning(currency_ledger.c.id)
            )
        ).first()
        return row is not None

    async def has_xp(self, op_id: str) -> bool:
        """Есть ли проводка опыта."""
        found = await self._s.scalar(select(xp_ledger.c.id).where(xp_ledger.c.op_id == op_id))
        return found is not None

    async def has_currency(self, op_id: str) -> bool:
        """Есть ли проводка Версов."""
        found = await self._s.scalar(
            select(currency_ledger.c.id).where(currency_ledger.c.op_id == op_id)
        )
        return found is not None

    @staticmethod
    def _entry(r: Any) -> Entry:
        return Entry(
            op_id=r.op_id,
            user_id=r.user_id,
            direction=Direction(r.direction),
            amount=r.amount,
            bucket=Bucket(r.bucket),
            reason=r.reason,
            source_ref=r.source_ref,
            config_version=r.config_version,
            at=r.at,
            reversal_of=r.reversal_of,
            actor_id=r.actor_id,
        )

    async def currency(self, op_id: str) -> Entry | None:
        """Проводка Версов по ``op_id``."""
        row = (
            await self._s.execute(select(currency_ledger).where(currency_ledger.c.op_id == op_id))
        ).first()
        return None if row is None else self._entry(row)

    async def is_reversed(self, op_id: str) -> bool:
        """Есть ли сторно у проводки."""
        found = await self._s.scalar(
            select(currency_ledger.c.id).where(currency_ledger.c.reversal_of == op_id)
        )
        return found is not None

    async def recent_currency(self, user_id: UUID, limit: int) -> list[Entry]:
        """Последние проводки Версов, новые первыми."""
        rows = await self._s.execute(
            select(currency_ledger)
            .where(currency_ledger.c.user_id == user_id)
            .order_by(currency_ledger.c.at.desc(), currency_ledger.c.id.desc())
            .limit(limit)
        )
        return [self._entry(r) for r in rows]

    async def session_rows(
        self, user_id: UUID, source_ref: str, reasons: Collection[str]
    ) -> SessionLedgerRows:
        """Начисления игрока за сессию: чтение по индексам ``(user_id, source_ref)``."""
        x, c = xp_ledger.c, currency_ledger.c
        xp_rows = (
            await self._s.execute(
                select(x.id, x.op_id, x.amount, x.at)
                .where(x.user_id == user_id, x.source_ref == source_ref, x.reason.in_(reasons))
                .order_by(x.at, x.id)
            )
        ).all()
        currency_rows = (
            await self._s.execute(
                select(c.op_id, c.amount).where(
                    c.user_id == user_id,
                    c.source_ref == source_ref,
                    c.direction == Direction.CREDIT.value,
                    c.reason.in_(reasons),
                )
            )
        ).all()
        total_after: int | None = None
        if xp_rows:
            last = xp_rows[-1]
            total_after = int(
                await self._s.scalar(
                    select(func.coalesce(func.sum(x.amount), 0)).where(
                        x.user_id == user_id, tuple_(x.at, x.id) <= tuple_(last.at, last.id)
                    )
                )
                or 0
            )
        return SessionLedgerRows(
            xp=sum(r.amount for r in xp_rows),
            verses=sum(r.amount for r in currency_rows),
            total_xp_after=total_after,
            op_ids=tuple(r.op_id for r in (*xp_rows, *currency_rows)),
        )

    async def mismatches(self, limit: int) -> list[LedgerMismatch]:
        """Расхождения суммы журнала с балансом и опытом."""
        rows = await self._s.execute(_MISMATCH_SQL, {"limit": limit})
        return [
            LedgerMismatch(
                kind=r.kind,
                user_id=r.user_id,
                bucket=r.bucket,
                ledger_sum=int(r.ledger_sum),
                stored=int(r.stored),
            )
            for r in rows
        ]


class SqlBalances:
    """Балансы Версов."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def lock(self, user_id: UUID, bucket: Bucket, *, now: datetime) -> int:
        """Баланс под ``FOR UPDATE`` (строка создаётся нулевой)."""
        await self._s.execute(
            pg_insert(balances)
            .values(user_id=user_id, bucket=bucket.value, amount=0, updated_at=now)
            .on_conflict_do_nothing(index_elements=[balances.c.user_id, balances.c.bucket])
        )
        amount = await self._s.scalar(
            select(balances.c.amount)
            .where(balances.c.user_id == user_id, balances.c.bucket == bucket.value)
            .with_for_update()
        )
        return int(amount or 0)

    async def save(self, user_id: UUID, bucket: Bucket, amount: int, *, now: datetime) -> None:
        """Записать баланс (``CHECK amount >= 0``)."""
        await self._s.execute(
            update(balances)
            .where(balances.c.user_id == user_id, balances.c.bucket == bucket.value)
            .values(amount=amount, updated_at=now)
        )

    async def all(self, user_id: UUID) -> dict[Bucket, int]:
        """Балансы игрока."""
        rows = await self._s.execute(
            select(balances.c.bucket, balances.c.amount).where(balances.c.user_id == user_id)
        )
        return {Bucket(r.bucket): int(r.amount) for r in rows}


class SqlCounters:
    """Дневные счётчики."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @staticmethod
    def _counters(r: Any) -> DailyCounters:
        return DailyCounters(
            solo_xp=int(r.solo_xp),
            solo_verses=int(r.solo_verses),
            online_verses=int(r.online_verses),
            first_win_awarded=bool(r.first_win_awarded),
        )

    async def lock(self, user_id: UUID, day: date) -> DailyCounters:
        """Счётчики суток под ``FOR UPDATE`` (строка создаётся нулевой)."""
        await self._s.execute(
            pg_insert(daily_counters)
            .values(user_id=user_id, day=day)
            .on_conflict_do_nothing(index_elements=[daily_counters.c.user_id, daily_counters.c.day])
        )
        row = (
            await self._s.execute(
                select(daily_counters)
                .where(daily_counters.c.user_id == user_id, daily_counters.c.day == day)
                .with_for_update()
            )
        ).one()
        return self._counters(row)

    async def save(self, user_id: UUID, day: date, counters: DailyCounters) -> None:
        """Записать счётчики."""
        await self._s.execute(
            update(daily_counters)
            .where(daily_counters.c.user_id == user_id, daily_counters.c.day == day)
            .values(
                solo_xp=counters.solo_xp,
                solo_verses=counters.solo_verses,
                online_verses=counters.online_verses,
                first_win_awarded=counters.first_win_awarded,
            )
        )

    async def get(self, user_id: UUID, day: date) -> DailyCounters:
        """Счётчики без блокировки."""
        row = (
            await self._s.execute(
                select(daily_counters).where(
                    daily_counters.c.user_id == user_id, daily_counters.c.day == day
                )
            )
        ).first()
        return DailyCounters() if row is None else self._counters(row)


class SqlMarks:
    """Нулевые отметки предметов; кодом не удаляются."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def has(self, op_id: str) -> bool:
        """Есть ли отметка предмета."""
        found = await self._s.scalar(
            select(accrual_marks.c.op_id).where(accrual_marks.c.op_id == op_id)
        )
        return found is not None

    async def add(self, op_id: str, user_id: UUID, reason: str, now: datetime) -> bool:
        """Поставить отметку; `False` - уже есть."""
        row = (
            await self._s.execute(
                pg_insert(accrual_marks)
                .values(op_id=op_id, user_id=user_id, reason=reason, at=now)
                .on_conflict_do_nothing(index_elements=[accrual_marks.c.op_id])
                .returning(accrual_marks.c.op_id)
            )
        ).first()
        return row is not None


class SqlStreaks:
    """Серии дейлика."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @staticmethod
    def _streak(r: Any) -> Streak:
        return Streak(current=r.current, best=r.best, last_day=r.last_day)

    async def lock(self, user_id: UUID, domain: str) -> Streak:
        """Серия домена под ``FOR UPDATE``."""
        row = (
            await self._s.execute(
                select(daily_streaks)
                .where(daily_streaks.c.user_id == user_id, daily_streaks.c.domain == domain)
                .with_for_update()
            )
        ).first()
        return Streak() if row is None else self._streak(row)

    async def save(self, user_id: UUID, domain: str, streak: Streak) -> None:
        """Записать серию."""
        values = {"current": streak.current, "best": streak.best, "last_day": streak.last_day}
        await self._s.execute(
            pg_insert(daily_streaks)
            .values(user_id=user_id, domain=domain, **values)
            .on_conflict_do_update(
                index_elements=[daily_streaks.c.user_id, daily_streaks.c.domain], set_=values
            )
        )

    async def all(self, user_id: UUID) -> dict[str, Streak]:
        """Серии игрока по доменам."""
        rows = await self._s.execute(
            select(daily_streaks).where(daily_streaks.c.user_id == user_id)
        )
        return {r.domain: self._streak(r) for r in rows}


class SqlRewardPeriods:
    """Отметки «награда периода выдана»."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def mark(self, period_key: str, user_id: UUID, now: datetime) -> bool:
        """Отметить; ``False`` — уже награждён."""
        row = (
            await self._s.execute(
                pg_insert(reward_periods)
                .values(period_key=period_key, user_id=user_id, awarded_at=now)
                .on_conflict_do_nothing()
                .returning(reward_periods.c.user_id)
            )
        ).first()
        return row is not None


class SqlProgressionUnitOfWork:
    """Транзакция use-case с репозиториями и записью событий в outbox."""

    configs: SqlConfigs
    progress: SqlProgress
    ledger: SqlLedger
    balances: SqlBalances
    counters: SqlCounters
    marks: SqlMarks
    streaks: SqlStreaks
    rewards: SqlRewardPeriods
    quests: SqlQuests
    metrics: SqlMetrics
    badges: SqlBadges
    spins: SqlSpins
    boosts: SqlBoosts
    grants: SqlGrants
    items: SqlItems
    inventory: SqlInventory
    equipped: SqlEquipped
    promos: SqlPromos
    packs: SqlPacks
    avatars: SqlAvatars
    events: SqlEvents
    reconcile: SqlReconcileCheckpoints
    retention: SqlRetention

    def __init__(
        self,
        *,
        producer: str,
        clock: Clock,
        sessions: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        if (sessions is None) == (session is None):
            raise ValueError("нужен ровно один из sessions или session")
        self._sessions = sessions
        self._external = session
        self._outbox = OutboxWriter(TABLES.outbox)
        self._producer = producer
        self._clock = clock
        self._session: AsyncSession | None = None
        self._tx: AsyncSessionTransaction | None = None
        self._events: list[DomainEvent] = []
        self._committed = False

    async def __aenter__(self) -> Self:
        if self._session is not None:
            raise RuntimeError("Unit of Work одноразовый")
        if self._external is not None:
            self._session = self._external
            self._tx = await self._session.begin_nested()
        else:
            assert self._sessions is not None  # noqa: S101 — проверено в __init__
            self._session = self._sessions()
            self._tx = await self._session.begin()
        s = self._session
        self.configs = SqlConfigs(s)
        self.progress = SqlProgress(s)
        self.ledger = SqlLedger(s)
        self.balances = SqlBalances(s)
        self.counters = SqlCounters(s)
        self.marks = SqlMarks(s)
        self.streaks = SqlStreaks(s)
        self.rewards = SqlRewardPeriods(s)
        self.quests = SqlQuests(s)
        self.metrics = SqlMetrics(s)
        self.badges = SqlBadges(s)
        self.spins = SqlSpins(s)
        self.boosts = SqlBoosts(s)
        self.grants = SqlGrants(s)
        self.items = SqlItems(s)
        self.inventory = SqlInventory(s)
        self.equipped = SqlEquipped(s)
        self.promos = SqlPromos(s)
        self.packs = SqlPacks(s)
        self.avatars = SqlAvatars(s)
        self.events = SqlEvents(s)
        self.reconcile = SqlReconcileCheckpoints(s)
        self.retention = SqlRetention(s)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if not self._committed:
                await self.rollback()
        finally:
            if self._external is None and self._session is not None:
                await self._session.close()

    async def read_only(self, *, statement_timeout_ms: int) -> None:
        """Сделать транзакцию только для чтения и ограничить время запроса.

        Вызывать до первого запроса транзакции (``SET TRANSACTION`` после запросов не пройдёт).
        ``set_config(..., true)`` — то же, что ``SET LOCAL``: действует до конца транзакции.
        """
        if self._session is None:
            raise RuntimeError("Unit of Work не открыт")
        await self._session.execute(text("SET TRANSACTION READ ONLY"))
        await self._session.execute(
            text("SELECT set_config('statement_timeout', :ms, true)"),
            {"ms": str(int(statement_timeout_ms))},
        )

    def publish(self, event: DomainEvent) -> None:
        """Событие уйдёт в outbox при коммите."""
        self._events.append(event)

    async def commit(self) -> None:
        """События — в outbox, затем коммит (или RELEASE SAVEPOINT)."""
        if self._tx is None or self._session is None:
            raise RuntimeError("Unit of Work не открыт")
        envelopes = [
            EventEnvelope.from_domain_event(event, producer=self._producer, clock=self._clock)
            for event in self._events
        ]
        await self._outbox.add(self._session, envelopes)
        self._events = []
        await self._tx.commit()
        self._committed = True

    async def rollback(self) -> None:
        """Откатить (транзакцию или SAVEPOINT)."""
        if self._tx is not None and self._tx.is_active:
            await self._tx.rollback()
        self._events = []
