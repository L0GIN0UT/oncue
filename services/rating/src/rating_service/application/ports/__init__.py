"""Порты application rating: репозитории, Unit of Work, лидерборд-хранилище."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from types import TracebackType
from typing import Any, Literal, Protocol, Self
from uuid import UUID

from gv_core.domain import AggregateRoot
from gv_db.idempotency import IdempotencyStore
from rating_service.domain.events import RatingChanged
from rating_service.domain.ladder import LadderConfig
from rating_service.domain.ladder_line import LadderLine
from rating_service.domain.rating import Rating
from rating_service.domain.season import Season

# Any: тело ответов — JSON по контракту OpenAPI (собирают представления, проверяет API-схема).
type Json = dict[str, Any]
type LockMode = Literal["share", "update"]


@dataclass(frozen=True, slots=True)
class AdminAction:
    """Запись аудита admin-действия rating."""

    actor_id: UUID
    action: str
    target: str
    reason: str
    at: datetime
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PublishedLadder:
    """Действующая версия конфига ladder-а."""

    version: int
    config: LadderConfig


@dataclass(frozen=True, slots=True)
class LadderSummary:
    """Ladder в списке админки."""

    key: str
    published_version: int | None
    draft_version: int | None


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """Строка истории рейтинга игрока."""

    season_id: UUID
    user_id: UUID
    match_id: UUID
    ladder: str
    at: datetime
    queue_format: str
    outcome: str
    old: int
    new: int
    delta: int
    old_tier: str | None
    tier: str | None
    calibrating: bool
    calibration_left: int
    leaver_penalty: int


@dataclass(frozen=True, slots=True)
class RebuildRequest:
    """Запрос админа на пересборку лидерборда."""

    request_id: UUID
    ladder: str
    season_id: UUID


@dataclass(frozen=True, slots=True)
class WeeklyRow:
    """Строка топа недели (до подстановки ников)."""

    user_id: UUID
    delta: int
    season_id: UUID


class LadderRepository(Protocol):
    """Ladder-ы и версии конфигов."""

    async def get_line(self, key: str, *, for_update: bool = False) -> LadderLine | None:
        """Линия версий или ``None``."""
        ...

    async def save_line(self, line: LadderLine) -> None:
        """Сохранить ladder и все его версии (upsert)."""
        ...

    async def summaries(self) -> list[LadderSummary]:
        """Все ladder-ы для админки."""
        ...

    async def published(self, key: str) -> PublishedLadder | None:
        """Действующая версия ladder-а."""
        ...

    async def all_published(self) -> list[PublishedLadder]:
        """Действующие версии всех опубликованных ladder-ов."""
        ...


class SeasonRepository(Protocol):
    """Сезоны."""

    async def active(self, ladder: str, *, lock: LockMode | None = None) -> Season | None:
        """Действующий сезон; ``lock`` — блокировка (``share``: матч, ``update``: закрытие)."""
        ...

    async def get(self, season_id: UUID, *, for_update: bool = False) -> Season | None:
        """Сезон по идентификатору."""
        ...

    async def open_seasons(self, ladder: str) -> list[Season]:
        """Плановые и действующие сезоны ladder-а."""
        ...

    async def latest_finished(self, ladder: str) -> Season | None:
        """Последний завершённый сезон."""
        ...

    async def next_number(self, ladder: str) -> int:
        """Номер следующего сезона."""
        ...

    async def add(self, season: Season) -> None:
        """Добавить сезон (и партицию истории)."""
        ...

    async def save(self, season: Season) -> None:
        """Сохранить изменения сезона."""
        ...

    async def overlaps(self, ladder: str, starts_at: datetime, ends_at: datetime) -> bool:
        """Пересекается ли период с сезоном ladder-а (завершённый занимает до ``finished_at``)."""
        ...

    async def due(self, ladder: str, now: datetime) -> list[Season]:
        """Действующие сезоны ladder-а, у которых вышло время."""
        ...

    async def scheduled_ready(self, ladder: str, now: datetime) -> Season | None:
        """Плановый сезон, чьё время наступило."""
        ...


class RatingRepository(Protocol):
    """Рейтинги игроков."""

    async def lock(
        self, ladder: str, season_id: UUID, user_ids: Sequence[UUID]
    ) -> dict[UUID, Rating]:
        """Существующие рейтинги сезона под ``FOR UPDATE`` (в порядке user_id — без дедлоков)."""
        ...

    async def many(
        self, ladder: str, season_id: UUID, user_ids: Sequence[UUID]
    ) -> dict[UUID, Rating]:
        """Рейтинги сезона без блокировки (чтение)."""
        ...

    async def latest_before(self, user_id: UUID, ladder: str, season_id: UUID) -> Rating | None:
        """Последний рейтинг игрока в другом сезоне (для игрока без строки в текущем)."""
        ...

    async def ensure(self, rating: Rating) -> None:
        """Создать строку рейтинга, если её нет (не затирает созданную параллельным матчем)."""
        ...

    async def save(self, rating: Rating) -> None:
        """Upsert рейтинга."""
        ...

    async def carry_over(
        self, old_season: UUID, new_season: UUID, config: LadderConfig, now: datetime
    ) -> int:
        """Скопировать рейтинги прошлого сезона в новый с мягким сбросом; число игроков."""
        ...

    async def count(self, season_id: UUID) -> int:
        """Игроков с рейтингом в сезоне."""
        ...

    async def get(self, user_id: UUID, ladder: str, season_id: UUID) -> Rating | None:
        """Рейтинг игрока в сезоне."""
        ...

    def board(
        self, ladder: str, season_id: UUID, *, min_matches: int, calibration_matches: int
    ) -> AsyncIterator[tuple[UUID, int, int]]:
        """Игроки лидерборда ``(user_id, rating, event_version)``: без калибровки и удалённых."""
        ...


class HistoryRepository(Protocol):
    """История рейтинга."""

    async def add(self, event: RatingChanged) -> None:
        """Записать строку истории по событию."""
        ...

    async def page(
        self, user_id: UUID, ladder: str, *, before: tuple[datetime, UUID] | None, limit: int
    ) -> list[HistoryRow]:
        """Строки игрока, новые первыми, строго старше ``before`` (at, match_id)."""
        ...

    async def for_match(self, match_id: UUID) -> list[HistoryRow]:
        """Строки обоих игроков матча."""
        ...


class WeeklyRepository(Protocol):
    """Прирост рейтинга по неделям и закрытые топы."""

    async def add_delta(
        self, ladder: str, week: date, user_id: UUID, *, season_id: UUID, delta: int, now: datetime
    ) -> None:
        """Накопить прирост игрока за неделю."""
        ...

    async def top(
        self, ladder: str, week: date, *, limit: int, calibration_matches: int
    ) -> list[WeeklyRow]:
        """Лучшие приросты (только положительные), без калибровки и удалённых."""
        ...

    async def rank_of(
        self, ladder: str, week: date, user_id: UUID, *, calibration_matches: int
    ) -> tuple[int, int] | None:
        """``(прирост, место)`` игрока в топе недели; ``None`` — игрока в топе нет."""
        ...

    async def delta_of(self, ladder: str, week: date, user_id: UUID) -> int | None:
        """Прирост игрока за неделю."""
        ...

    async def pending_weeks(self, ladder: str, before: date) -> list[date]:
        """Недели до ``before`` с приростами, ещё не закрытые."""
        ...

    async def is_finalized(self, ladder: str, week: date) -> bool:
        """Закрыта ли неделя (топ уже объявлен)."""
        ...

    async def week_season(self, ladder: str, week: date) -> UUID | None:
        """Сезон последнего прироста недели — он и значит «сезон» ``WeeklyTopFinalized``."""
        ...

    async def finalize(
        self, ladder: str, week: date, season_id: UUID | None, top: list[Json], now: datetime
    ) -> bool:
        """Закрыть неделю; ``False`` — уже закрыта (идемпотентность по периоду)."""
        ...


class ProcessedMatches(Protocol):
    """Дедупликация по ``match_id`` поверх inbox."""

    async def mark(self, match_id: UUID, ladder: str, now: datetime) -> bool:
        """Отметить матч; ``False`` — уже обработан."""
        ...


class UserProjection(Protocol):
    """Проекция профилей identity (ники)."""

    async def profile(self, user_id: UUID, nickname: str, version: int) -> None:
        """Ник из ``UserRegistered``/``UserProfileBasicsChanged`` (устаревшая версия — мимо)."""
        ...

    async def forget(self, user_id: UUID, version: int) -> None:
        """``UserDeleted``: ник стёрт, пользователь скрыт."""
        ...

    async def nicknames(self, user_ids: Sequence[UUID]) -> dict[UUID, str]:
        """Ники живых пользователей."""
        ...

    async def is_visible(self, user_id: UUID) -> bool:
        """Известен ли пользователь и не удалён ли он."""
        ...

    async def deleted(self, user_ids: Sequence[UUID]) -> set[UUID]:
        """Какие из пользователей удалены."""
        ...

    async def rated_seasons(self, user_id: UUID) -> list[tuple[str, UUID]]:
        """``(ladder, season_id)`` всех рейтингов пользователя (чистка лидербордов)."""
        ...


class RebuildQueue(Protocol):
    """Запросы админа на пересборку лидерборда."""

    async def request(
        self, ladder: str, season_id: UUID, reason: str, actor_id: UUID, now: datetime
    ) -> UUID:
        """Поставить запрос."""
        ...

    async def pending(self) -> list[RebuildRequest]:
        """Невыполненные запросы, старые первыми."""
        ...

    async def complete(self, request_id: UUID, now: datetime, error: str | None = None) -> None:
        """Отметить закрытым (``error`` — закрыт без пересборки)."""
        ...


class AuditLog(Protocol):
    """Аудит admin-действий (append-only)."""

    async def record(self, action: AdminAction) -> None:
        """Записать действие."""
        ...


class RatingUnitOfWork(Protocol):
    """Транзакция use-case с репозиториями и записью событий в outbox."""

    ladders: LadderRepository
    seasons: SeasonRepository
    ratings: RatingRepository
    history: HistoryRepository
    weekly: WeeklyRepository
    matches: ProcessedMatches
    users: UserProjection
    rebuilds: RebuildQueue
    audit: AuditLog
    idempotency: IdempotencyStore

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def track(self, aggregate: AggregateRoot[Any]) -> None:
        """Отметить агрегат: его события попадут в outbox при коммите."""
        ...

    async def commit(self) -> None:
        """Записать события в outbox и закоммитить (или RELEASE SAVEPOINT)."""
        ...


UnitOfWorkFactory = Callable[[], RatingUnitOfWork]


class LeaderboardStore(Protocol):
    """Лидерборды для чтения (Redis ZSET); источник истины — Postgres."""

    async def put(
        self, ladder: str, season_id: UUID, user_id: UUID, rating: int, version: int
    ) -> None:
        """Поставить игрока (или обновить рейтинг), если ``version`` новее записанной."""
        ...

    async def remove(self, ladder: str, season_id: UUID, user_id: UUID, version: int) -> None:
        """Убрать игрока, если ``version`` новее записанной (запоминается как «надгробие»)."""
        ...

    async def replace(
        self,
        ladder: str,
        season_id: UUID,
        entries: Sequence[tuple[UUID, int, int]],
        *,
        expires: bool,
    ) -> None:
        """Атомарно заменить лидерборд сезона записями ``(user_id, rating, event_version)``."""
        ...

    async def exists(self, ladder: str, season_id: UUID) -> bool:
        """Есть ли хранилище сезона (потеря Redis)."""
        ...

    async def claim_rebuild(self, ladder: str, season_id: UUID, ttl_s: int) -> bool:
        """Занять право поставить пересборку (``SET NX``); ``False`` — уже занято."""
        ...

    async def page(
        self, ladder: str, season_id: UUID, offset: int, limit: int
    ) -> list[tuple[UUID, int]]:
        """Игроки с ``offset`` по убыванию рейтинга."""
        ...

    async def rank(self, ladder: str, season_id: UUID, user_id: UUID) -> int | None:
        """Место (с 0) или ``None`` — игрока нет в лидерборде."""
        ...

    async def size(self, ladder: str, season_id: UUID) -> int:
        """Размер лидерборда."""
        ...
