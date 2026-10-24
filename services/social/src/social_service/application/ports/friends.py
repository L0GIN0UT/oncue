"""Порты друзей: репозиторий, Unit of Work, присутствие, лимитер (ADR 0050)."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from gv_core.domain import DomainEvent
from gv_db.idempotency import IdempotencyStore
from social_service.domain.friends import Privacy


@dataclass(frozen=True, slots=True)
class RequestRow:
    """Заявка в друзья."""

    id: UUID
    from_user_id: UUID
    to_user_id: UUID
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FriendRow:
    """Друг в списке владельца."""

    user_id: UUID
    nickname: str
    since: datetime


@dataclass(frozen=True, slots=True)
class BlockRow:
    """Заблокированный игрок."""

    user_id: UUID
    nickname: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PendingView:
    """Ожидающая заявка с ником второй стороны."""

    id: UUID
    user_id: UUID
    nickname: str
    created_at: datetime
    incoming: bool


@dataclass(frozen=True, slots=True)
class DeletedFriendship:
    """Разорванная дружба (для события)."""

    user_a: UUID
    user_b: UUID


class FriendsRepository(Protocol):
    """Данные друзей, блокировок, приватности и проекций (одна транзакция)."""

    # --- блокировки порядка (гонки)
    async def lock_users(self, *user_ids: UUID) -> None:
        """Транзакционные блокировки игроков в порядке возрастания (лимиты)."""
        ...

    async def lock_pair(self, a: UUID, b: UUID) -> None:
        """Транзакционная блокировка пары: все изменения пары идут по очереди."""
        ...

    # --- проекция ников
    async def nickname(self, user_id: UUID) -> str | None:
        """Ник из проекции identity или ``None``."""
        ...

    async def user_by_nickname(self, nickname: str) -> UUID | None:
        """Игрок по нику (без учёта регистра)."""
        ...

    # --- коды
    async def code_of(self, user_id: UUID) -> tuple[str, datetime] | None:
        """Код игрока и время перевыпуска."""
        ...

    async def user_by_code(self, code: str) -> UUID | None:
        """Владелец действующего кода."""
        ...

    async def create_code(self, user_id: UUID, code: str, now: datetime) -> bool:
        """Создать код, если у игрока нет; ``False`` - занят чужим или у игрока уже есть."""
        ...

    async def rotate_code(self, user_id: UUID, code: str, now: datetime) -> bool:
        """Заменить код; ``False`` - код занят другим."""
        ...

    # --- блокировки
    async def blocked(self, blocker: UUID, blocked: UUID) -> bool:
        """``blocker`` заблокировал ``blocked``."""
        ...

    async def add_block(self, blocker: UUID, blocked: UUID, now: datetime) -> bool:
        """Добавить блокировку; ``False`` - уже была."""
        ...

    async def remove_block(self, blocker: UUID, blocked: UUID) -> bool:
        """Снять блокировку; ``False`` - не было."""
        ...

    async def blocks_of(self, blocker: UUID) -> list[BlockRow]:
        """Блокировки владельца, новые первыми (игроки без проекции пропускаются)."""
        ...

    # --- приватность
    async def privacy(self, user_id: UUID) -> Privacy:
        """Настройки или значения по умолчанию."""
        ...

    async def save_privacy(self, user_id: UUID, value: Privacy, now: datetime) -> None:
        """Заменить настройки целиком."""
        ...

    # --- дружба
    async def are_friends(self, a: UUID, b: UUID) -> bool:
        """Есть дружба."""
        ...

    async def add_friendship(self, a: UUID, b: UUID, now: datetime) -> bool:
        """Создать дружбу; ``False`` - уже была."""
        ...

    async def remove_friendship(self, a: UUID, b: UUID) -> bool:
        """Разорвать; ``False`` - не было."""
        ...

    async def count_friends(self, user_id: UUID) -> int:
        """Число друзей."""
        ...

    async def friends_of(self, user_id: UUID) -> list[FriendRow]:
        """Друзья (с проекцией ника), по нику."""
        ...

    async def in_match(self, user_ids: Collection[UUID]) -> set[UUID]:
        """Кто из игроков сейчас в матче."""
        ...

    # --- заявки
    async def add_request(self, row: RequestRow) -> None:
        """Сохранить ожидающую заявку."""
        ...

    async def get_request(self, request_id: UUID, *, for_update: bool = False) -> RequestRow | None:
        """Заявка по id (``for_update`` - блокировка строки на время транзакции)."""
        ...

    async def pending_from_to(self, from_user: UUID, to_user: UUID) -> RequestRow | None:
        """Ожидающая заявка из ``from_user`` в ``to_user``."""
        ...

    async def close_request(self, request_id: UUID, status: str, now: datetime) -> None:
        """Перевести заявку из ожидания в ``accepted | declined | cancelled``."""
        ...

    async def close_pending_between(self, a: UUID, b: UUID, now: datetime) -> int:
        """Отозвать ожидающие заявки пары в обе стороны."""
        ...

    async def sent_since(self, user_id: UUID, since: datetime) -> int:
        """Сколько заявок игрок отправил с момента ``since``."""
        ...

    async def pending_of(self, user_id: UUID) -> list[PendingView]:
        """Ожидающие заявки игрока (входящие и исходящие)."""
        ...

    # --- проекции событий
    async def upsert_user(self, user_id: UUID, nickname: str, version: int, now: datetime) -> bool:
        """Записать ник, если ``version`` новее применённой; ``False`` - отброшено."""
        ...

    async def match_started(self, match_id: UUID, user_ids: Sequence[UUID], now: datetime) -> None:
        """Отметить игроков «в матче»."""
        ...

    async def match_finished(self, match_id: UUID) -> None:
        """Снять отметку «в матче» по матчу."""
        ...

    async def erase_user(self, user_id: UUID) -> list[DeletedFriendship]:
        """Удалить все данные игрока; вернуть разорванные дружбы."""
        ...


class FriendsUnitOfWork(Protocol):
    """Транзакция use-case друзей; события уходят в outbox при коммите."""

    @property
    def repo(self) -> FriendsRepository:
        """Репозиторий (доступен внутри ``async with``)."""
        ...

    @property
    def idempotency(self) -> IdempotencyStore:
        """Ключи идемпотентности текущей транзакции."""
        ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def publish(self, event: DomainEvent) -> None:
        """Событие уйдёт в outbox при коммите."""
        ...

    async def commit(self) -> None:
        """Записать события и закоммитить."""
        ...


class FriendsUowFactory(Protocol):
    """Фабрика Unit of Work."""

    def __call__(self) -> FriendsUnitOfWork: ...


@dataclass(frozen=True, slots=True)
class AttemptWindow:
    """Состояние окна лимитера после учтённой попытки."""

    count: int
    resets_in_s: int


class AttemptLimiter(Protocol):
    """Счётчик попыток в окне; недоступность - ``LimiterUnavailableError`` (fail-closed)."""

    async def hit(self, key: str, window_s: int) -> AttemptWindow:
        """Учесть попытку."""
        ...


class PresenceStore(Protocol):
    """Ключи присутствия (Redis с TTL)."""

    async def touch(self, user_id: UUID, ttl_s: int) -> None:
        """Отметить игрока онлайн на ``ttl_s`` секунд."""
        ...

    async def online(self, user_ids: Collection[UUID]) -> set[UUID]:
        """Кто онлайн; недоступность хранилища - пустое множество (деградация)."""
        ...
