"""Порты комментариев, жалоб и проекций social (5c, поток F2)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import TYPE_CHECKING, Protocol, Self
from uuid import UUID

from gv_core.domain import DomainEvent
from gv_db.idempotency import IdempotencyStore
from social_service.domain.discussion import CollectionRef, Comment

if TYPE_CHECKING:
    from social_service.application.ports.cases import AuditLog, AvatarProjection, CaseRepository


@dataclass(frozen=True, slots=True)
class NewReport:
    """Жалоба к записи."""

    id: UUID
    author_id: UUID
    target_kind: str
    target_id: UUID
    target_user_id: UUID
    context_match_id: UUID | None
    reason_code: str
    text: str | None
    created_at: datetime


class CommentRepository(Protocol):
    """Комментарии."""

    async def add(self, comment: Comment) -> None:
        """Сохранить новый комментарий."""
        ...

    async def get(self, comment_id: UUID, *, for_update: bool = False) -> Comment | None:
        """Комментарий (в том числе удалённый) или ``None``; ``for_update`` блокирует строку."""
        ...

    async def set_body(self, comment_id: UUID, body: str, edited_at: datetime) -> None:
        """Заменить текст."""
        ...

    async def mark_deleted(
        self, comment_id: UUID, *, at: datetime, by: UUID | None, role: str
    ) -> bool:
        """Мягко удалить и очистить текст; ``False`` - уже удалён."""
        ...

    async def page(
        self, collection_id: UUID, *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[Comment]:
        """Живые комментарии, новые первыми, после позиции курсора."""
        ...

    async def count(self, collection_id: UUID) -> int:
        """Число живых комментариев сборника."""
        ...

    async def anonymize_author(self, user_id: UUID, *, at: datetime) -> list[Comment]:
        """Удалить все живые комментарии автора (аккаунт удалён); вернуть удалённые."""
        ...


class CollectionProjection(Protocol):
    """Проекция сборников."""

    async def get(self, collection_id: UUID) -> CollectionRef | None:
        """Сборник или ``None``."""
        ...

    async def apply(self, ref: CollectionRef) -> None:
        """Применить снимок, если его ``version`` новее сохранённой."""
        ...


class Nicknames(Protocol):
    """Ники авторов (проекция identity, пишет поток друзей)."""

    async def get(self, user_ids: list[UUID]) -> dict[UUID, str]:
        """Ники известных игроков; неизвестные в ответе отсутствуют."""
        ...


class Blocks(Protocol):
    """Блокировки игроков (таблица ведёт поток друзей; здесь только чтение)."""

    async def is_blocked(self, blocker: UUID, blocked: UUID) -> bool:
        """Заблокировал ли ``blocker`` игрока ``blocked``."""
        ...


class ReportRepository(Protocol):
    """Жалобы и кейсы. Операции вызываются в одной транзакции под блокировкой объекта."""

    async def lock_target(self, kind: str, target_id: UUID) -> None:
        """Сериализовать жалобы на один объект (advisory lock до конца транзакции)."""
        ...

    async def insert(self, report: NewReport) -> UUID | None:
        """Записать жалобу; ``None`` - автор уже жаловался на объект."""
        ...

    async def existing_id(self, author_id: UUID, kind: str, target_id: UUID) -> UUID:
        """Идентификатор прежней жалобы автора на объект."""
        ...

    async def count_by_author_since(self, author_id: UUID, since: datetime) -> int:
        """Сколько жалоб автор подал с момента ``since``."""
        ...

    async def unassigned_ids(self, kind: str, target_id: UUID, since: datetime) -> list[UUID]:
        """Жалобы на объект с момента ``since``, не привязанные ни к одному кейсу.

        Жалобы, уже учтённые кейсом (в том числе решённым), порог не набирают.
        """
        ...

    async def open_case(
        self, case_id: UUID, kind: str, target_id: UUID, target_user_id: UUID, now: datetime
    ) -> UUID | None:
        """Открыть кейс; ``None`` - у объекта уже есть неразрешённый кейс."""
        ...

    async def active_case_id(self, kind: str, target_id: UUID) -> UUID | None:
        """Неразрешённый кейс объекта."""
        ...

    async def attach(self, case_id: UUID, report_ids: list[UUID]) -> None:
        """Добавить жалобы в кейс (повтор игнорируется)."""
        ...

    async def anonymize_author(self, user_id: UUID) -> int:
        """Очистить тексты жалоб удалённого аккаунта."""
        ...


class MatchRepository(Protocol):
    """Участники матчей и счётчики порядочности."""

    async def add_participants(
        self, match_id: UUID, user_ids: list[UUID], *, solo: bool, finished_at: datetime
    ) -> list[UUID]:
        """Записать участников; вернуть тех, кого записали впервые."""
        ...

    async def shared_match(self, match_id: UUID, first: UUID, second: UUID) -> bool:
        """Участвовали ли оба в несоло-матче ``match_id``."""
        ...

    async def bump(self, user_id: UUID, field: str, by: int = 1) -> None:
        """Увеличить счётчик порядочности игрока."""
        ...


class DiscussionUnitOfWork(Protocol):
    """Транзакция use-case: репозитории и события (в outbox при коммите)."""

    # Свойства только для чтения: реализации объявляют атрибуты конкретных классов.
    @property
    def comments(self) -> CommentRepository: ...

    @property
    def collections(self) -> CollectionProjection: ...

    @property
    def nicknames(self) -> Nicknames: ...

    @property
    def reports(self) -> ReportRepository: ...

    @property
    def matches(self) -> MatchRepository: ...

    @property
    def blocks(self) -> Blocks: ...

    @property
    def cases(self) -> CaseRepository: ...

    @property
    def avatars(self) -> AvatarProjection: ...

    @property
    def audit(self) -> AuditLog: ...

    @property
    def idempotency(self) -> IdempotencyStore: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def emit(self, event: DomainEvent) -> None:
        """Событие уйдёт в outbox при коммите."""
        ...

    async def commit(self) -> None:
        """Зафиксировать данные и события."""
        ...


type DiscussionUowFactory = Callable[[], DiscussionUnitOfWork]


class RateLimiter(Protocol):
    """Счётчик попыток в окне; недоступное хранилище - ``DependencyUnavailableError``."""

    async def hit(self, subject: str, *, window_s: int) -> int:
        """Учесть попытку и вернуть её номер в окне."""
        ...
