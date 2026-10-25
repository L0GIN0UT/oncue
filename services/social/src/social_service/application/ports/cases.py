"""Порты кейсов модерации, проекции аватарок и аудита (5c, поток F4)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from social_service.application.ports.discussion import DiscussionUnitOfWork
from social_service.application.ports.sanctions import SanctionGranter
from social_service.domain.cases import ModerationCase


@dataclass(frozen=True, slots=True)
class CaseReportRow:
    """Жалоба кейса (с текстом - только для админ-ответа)."""

    id: UUID
    author_id: UUID
    reason_code: str
    text: str | None
    context_match_id: UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AvatarRef:
    """Аватарка в проекции ``media_pipeline.AssetModerated``."""

    asset_id: UUID
    owner_user_id: UUID
    status: str  # approved | rejected
    version: int


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """Запись аудита ручного действия (причина обязательна)."""

    id: UUID
    actor_id: UUID
    action: str
    target: str
    reason: str
    at: datetime
    details: dict[str, Any] = field(default_factory=dict)


class CaseRepository(Protocol):
    """Кейсы модерации."""

    async def get(self, case_id: UUID, *, for_update: bool = False) -> ModerationCase | None:
        """Кейс или ``None``; ``for_update`` блокирует строку."""
        ...

    async def page(
        self,
        *,
        status: str | None,
        target_kind: str | None,
        assignee_id: UUID | None,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[ModerationCase]:
        """Кейсы, старые первыми, после позиции курсора."""
        ...

    async def save(self, case: ModerationCase, *, expected_version: int) -> bool:
        """Записать новое состояние, если версия в БД равна ``expected_version``."""
        ...

    async def reports(self, case_id: UUID) -> list[CaseReportRow]:
        """Жалобы кейса, старые первыми."""
        ...

    async def preview(self, kind: str, target_id: UUID) -> str | None:
        """Фрагмент содержимого объекта (комментарий, название сборника) или ``None``."""
        ...


class AvatarProjection(Protocol):
    """Владельцы UGC-аватарок."""

    async def get(self, asset_id: UUID) -> AvatarRef | None:
        """Аватарка или ``None``."""
        ...

    async def apply(self, ref: AvatarRef) -> None:
        """Применить решение, если ``version`` новее сохранённой."""
        ...


class StaffPermsProjection(Protocol):
    """Эффективные права пользователей из ``identity.UserRolesChanged`` (иерархия модераторов)."""

    async def apply(self, user_id: UUID, perms: frozenset[str], version: int) -> None:
        """Сохранить снимок, если ``version`` новее сохранённой."""
        ...

    async def delete(self, user_id: UUID) -> None:
        """Удалить запись (аккаунт удалён)."""
        ...


class AuditLog(Protocol):
    """Аудит ручных действий модераторов и конфига."""

    async def add(self, entry: AuditEntry) -> None:
        """Записать действие в транзакции вызывающего."""
        ...


type GranterFactory = Callable[[DiscussionUnitOfWork], SanctionGranter]
"""Выдача санкции в транзакции переданного UoW (кейс решается атомарно с санкцией)."""
