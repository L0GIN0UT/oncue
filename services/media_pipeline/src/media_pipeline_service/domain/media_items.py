"""Локальная проекция медиа-единицы каталога (статус и версия).

Чужую БД не читаем: состояние единицы приходит событиями ``catalog.MediaItem*``. Порядок
внутри агрегата гарантирует партиция, но доставка «хотя бы один раз» и пересоздание
потребителя могут вернуть старое событие — его отбрасываем по ``version``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from gv_core.domain import Entity


class ItemStatus(StrEnum):
    """Статус единицы в каталоге."""

    ACTIVE = "active"
    BLOCKED = "blocked"


class MediaItemState(Entity[UUID]):
    """Проекция единицы: домен, вид, статус и последняя применённая версия.

    Attributes:
        domain: ``music`` | ``film``; ``None``, если первым пришло событие без снимка.
        kind: ``track`` | ``film`` (или ``None``).
        status: Активна или заблокирована.
        version: Версия последнего применённого события catalog.
        updated_at: Когда проекция менялась.
    """

    def __init__(
        self,
        id: UUID,  # noqa: A002
        *,
        domain: str | None,
        kind: str | None,
        status: ItemStatus,
        version: int,
        updated_at: datetime,
    ) -> None:
        super().__init__(id)
        self.domain = domain
        self.kind = kind
        self.status = status
        self.version = version
        self.updated_at = updated_at

    @property
    def is_blocked(self) -> bool:
        """Единица заблокирована."""
        return self.status is ItemStatus.BLOCKED

    def is_stale(self, version: int) -> bool:
        """Событие с такой версией уже применено или устарело."""
        return version <= self.version

    def apply_snapshot(
        self, *, version: int, domain: str, kind: str, status: ItemStatus, now: datetime
    ) -> bool:
        """Применить полный снимок (``Created``/``Updated``). ``False`` — событие устарело."""
        if self.is_stale(version):
            return False
        self.domain, self.kind, self.status = domain, kind, status
        self.version, self.updated_at = version, now
        return True

    def apply_status(self, *, version: int, status: ItemStatus, now: datetime) -> bool:
        """Применить блокировку или разблокировку. ``False`` — событие устарело."""
        if self.is_stale(version):
            return False
        self.status, self.version, self.updated_at = status, version, now
        return True
