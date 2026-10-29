"""Порты progression: репозитории, Unit of Work, хук наград. Реализации — в infrastructure."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from gv_core.domain import DomainEvent
from progression_service.application.ports.avatar import AvatarRepository
from progression_service.application.ports.events import EventRepository
from progression_service.application.ports.ops import (
    ReconcileCheckpointRepository,
    RetentionRepository,
)
from progression_service.application.ports.rewards import (
    BadgeRepository,
    BoostRepository,
    GrantRepository,
    MetricRepository,
    QuestRepository,
    SpinRepository,
)
from progression_service.application.ports.shop import (
    EquippedRepository,
    InventoryRepository,
    ItemRepository,
    PackRepository,
    PromoRepository,
)
from progression_service.domain.config import ProgressionConfig
from progression_service.domain.daily_streak import Streak
from progression_service.domain.ledger import Bucket, Entry


@dataclass(frozen=True, slots=True)
class PublishedConfig:
    """Действующая версия конфига."""

    version: int
    config: ProgressionConfig


@dataclass(slots=True)
class ProgressRow:
    """Прогресс игрока (``progress``)."""

    user_id: UUID
    total_xp: int
    level: int
    config_version: int
    updated_at: datetime
    hidden: bool = False


@dataclass(slots=True)
class DailyCounters:
    """Дневные счётчики игрока: опыт и Версы с соло, Версы онлайна, выдана ли первая победа дня."""

    solo_xp: int = 0
    solo_verses: int = 0
    online_verses: int = 0
    first_win_awarded: bool = False


@dataclass(frozen=True, slots=True)
class XpEntry:
    """Проводка журнала опыта (``amount`` уже с бустом)."""

    op_id: str
    user_id: UUID
    amount: int
    base_amount: int
    boost_multiplier: Decimal
    reason: str
    source_ref: str
    config_version: int
    at: datetime


@dataclass(frozen=True, slots=True)
class LedgerMismatch:
    """Расхождение суммы журнала и таблицы-проекции."""

    kind: str
    user_id: UUID
    bucket: str | None
    ledger_sum: int
    stored: int


class ConfigRepository(Protocol):
    """Версии конфига."""

    async def published(self) -> PublishedConfig | None:
        """Действующая версия или ``None``."""
        ...

    async def effective_at(self, moment: datetime) -> PublishedConfig | None:
        """Версия, действовавшая в ``moment`` (ADR 0042: версия на ``finished_at``).

        Среди когда-либо опубликованных (в том числе архивных) — с наибольшим
        ``published_at <= moment``; если ``moment`` раньше первой публикации — самая ранняя;
        ``None`` — опубликованных версий нет.
        """
        ...


class ProgressRepository(Protocol):
    """Прогресс игрока."""

    async def lock(self, user_id: UUID, *, config_version: int, now: datetime) -> ProgressRow:
        """Создать строку (уровень 1) при отсутствии и взять ``FOR UPDATE``."""
        ...

    async def get(self, user_id: UUID) -> ProgressRow | None:
        """Прогресс без блокировки."""
        ...

    async def many(self, user_ids: Sequence[UUID]) -> list[ProgressRow]:
        """Прогресс пачки игроков без блокировки (отсутствующие строки не возвращаются)."""
        ...

    async def save(self, row: ProgressRow) -> None:
        """Сохранить опыт, уровень и версию конфига."""
        ...


class LedgerRepository(Protocol):
    """Журналы (только добавление)."""

    async def add_xp(self, entry: XpEntry) -> bool:
        """Добавить проводку опыта; ``False`` — ``op_id`` уже есть (ничего не изменено)."""
        ...

    async def add_currency(self, entry: Entry) -> bool:
        """Добавить проводку Версов; ``False`` — ``op_id`` уже есть."""
        ...

    async def has_xp(self, op_id: str) -> bool:
        """Есть ли проводка опыта с таким ``op_id``."""
        ...

    async def has_currency(self, op_id: str) -> bool:
        """Есть ли проводка Версов с таким ``op_id``."""
        ...

    async def currency(self, op_id: str) -> Entry | None:
        """Проводка Версов по ``op_id``."""
        ...

    async def is_reversed(self, op_id: str) -> bool:
        """Есть ли сторно у проводки Версов."""
        ...

    async def recent_currency(self, user_id: UUID, limit: int) -> list[Entry]:
        """Последние проводки Версов игрока, новые первыми."""
        ...

    async def mismatches(self, limit: int) -> list[LedgerMismatch]:
        """Расхождения «сумма журнала ≠ баланс/опыт» (не больше ``limit``)."""
        ...

    async def next_chunk(
        self, ledger: str, after: UUID | None, bound: UUID, batch: int
    ) -> tuple[UUID | None, int]:
        """Кусок проводок журнала ``ledger`` с ``after < id <= bound``, не больше ``batch``.

        Возвращает ``(id последней проводки куска, сколько проводок в куске)``; ``None`` - пусто.
        """
        ...

    async def mismatches_in_range(
        self, ledger: str, after: UUID | None, upto: UUID, limit: int
    ) -> list[LedgerMismatch]:
        """Расхождения у игроков с проводками ``after < id <= upto`` (суммы - по всему журналу)."""
        ...


class BalanceRepository(Protocol):
    """Балансы Версов по бакетам."""

    async def lock(self, user_id: UUID, bucket: Bucket, *, now: datetime) -> int:
        """Баланс бакета под ``FOR UPDATE`` (строка создаётся нулевой)."""
        ...

    async def save(self, user_id: UUID, bucket: Bucket, amount: int, *, now: datetime) -> None:
        """Записать новый баланс (``CHECK amount >= 0`` в схеме)."""
        ...

    async def all(self, user_id: UUID) -> dict[Bucket, int]:
        """Балансы игрока без блокировки."""
        ...


class CounterRepository(Protocol):
    """Дневные счётчики."""

    async def lock(self, user_id: UUID, day: date) -> DailyCounters:
        """Счётчики суток под ``FOR UPDATE`` (строка создаётся нулевой)."""
        ...

    async def save(self, user_id: UUID, day: date, counters: DailyCounters) -> None:
        """Записать счётчики."""
        ...

    async def get(self, user_id: UUID, day: date) -> DailyCounters:
        """Счётчики без блокировки (нет строки — нули)."""
        ...


class MarkRepository(Protocol):
    """Нулевые отметки предметов (``accrual_marks``): сумма после потолка 0, проводки нет."""

    async def has(self, op_id: str) -> bool:
        """Есть ли отметка предмета."""
        ...

    async def add(self, op_id: str, user_id: UUID, reason: str, now: datetime) -> bool:
        """Поставить отметку; `False` - уже есть."""
        ...


class StreakRepository(Protocol):
    """Серии дейлика."""

    async def lock(self, user_id: UUID, domain: str) -> Streak:
        """Серия домена под ``FOR UPDATE`` (нет строки — пустая серия)."""
        ...

    async def save(self, user_id: UUID, domain: str, streak: Streak) -> None:
        """Записать серию."""
        ...

    async def all(self, user_id: UUID) -> dict[str, Streak]:
        """Серии игрока по доменам."""
        ...


class RewardPeriodRepository(Protocol):
    """Отметки «награда периода выдана»."""

    async def mark(self, period_key: str, user_id: UUID, now: datetime) -> bool:
        """Отметить; ``False`` — игрок уже награждён за этот период."""
        ...


class ProgressionUnitOfWork(Protocol):
    """Транзакция начисления: репозитории и запись событий в outbox при коммите."""

    @property
    def configs(self) -> ConfigRepository: ...

    @property
    def progress(self) -> ProgressRepository: ...

    @property
    def ledger(self) -> LedgerRepository: ...

    @property
    def balances(self) -> BalanceRepository: ...

    @property
    def counters(self) -> CounterRepository: ...

    @property
    def marks(self) -> MarkRepository: ...

    @property
    def streaks(self) -> StreakRepository: ...

    @property
    def rewards(self) -> RewardPeriodRepository: ...

    @property
    def quests(self) -> QuestRepository: ...

    @property
    def metrics(self) -> MetricRepository: ...

    @property
    def badges(self) -> BadgeRepository: ...

    @property
    def spins(self) -> SpinRepository: ...

    @property
    def boosts(self) -> BoostRepository: ...

    @property
    def grants(self) -> GrantRepository: ...

    @property
    def items(self) -> ItemRepository: ...

    @property
    def inventory(self) -> InventoryRepository: ...

    @property
    def equipped(self) -> EquippedRepository: ...

    @property
    def promos(self) -> PromoRepository: ...

    @property
    def packs(self) -> PackRepository: ...

    @property
    def avatars(self) -> AvatarRepository: ...

    @property
    def events(self) -> EventRepository: ...

    @property
    def reconcile(self) -> ReconcileCheckpointRepository: ...

    @property
    def retention(self) -> RetentionRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def read_only(self, *, statement_timeout_ms: int) -> None:
        """Транзакция только для чтения с потолком времени запроса (до первого запроса)."""
        ...

    def publish(self, event: DomainEvent) -> None:
        """Событие уйдёт в outbox вместе с коммитом."""
        ...

    async def commit(self) -> None:
        """Записать события в outbox и зафиксировать транзакцию (или SAVEPOINT)."""
        ...


UnitOfWorkFactory = Callable[[], ProgressionUnitOfWork]


class RewardHook(Protocol):
    """Точка расширения наград: P2 подключает бусты опыта, не меняя ядро начисления.

    Множитель применяется только к онлайн-опыту (матчи и «первая победа дня»), к соло — никогда
    (R3). Вызов должен быть идемпотентен по ``match_id``: повтор события не тратит буст дважды.
    Вызывается под блокировками ``FOR UPDATE`` игроков матча — I/O (сеть, чужая БД) внутри
    запрещено. Ядро режет результат границами конфига (не меньше 1, не больше ``maximum`` схемы).

    Хук получает UoW транзакции матча: трату буста он пишет в ней же (без внешнего I/O), поэтому
    откат матча возвращает буст, а повтор события его не тратит.
    """

    def online_xp_multiplier(
        self, uow: ProgressionUnitOfWork, user_id: UUID, match_id: UUID, now: datetime
    ) -> Awaitable[Decimal]:
        """Множитель опыта игрока в матче; ``1`` — буста нет."""
        ...


class NoopRewardHook:
    """Хук по умолчанию: буста нет."""

    async def online_xp_multiplier(
        self,
        uow: ProgressionUnitOfWork,  # noqa: ARG002
        user_id: UUID,  # noqa: ARG002
        match_id: UUID,  # noqa: ARG002
        now: datetime,  # noqa: ARG002
    ) -> Decimal:
        """Всегда ``1``."""
        return Decimal(1)
