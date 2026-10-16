"""Локальная проекция сборника: только версия и статус (ADR 0029).

Состав и свои загадки сборника media-pipeline отдельно не хранит: эмодзи становятся
текстовыми исходниками со ``scope`` сборника, а версия нужна, чтобы отбросить старое или
повторное событие ``collections.collection.v1`` (порядок внутри агрегата даёт партиция, но
доставка «хотя бы один раз» может вернуть старое). Удалённый сборник остаётся строкой со
статусом ``deleted`` (надгробие): запоздавший ``CollectionUpdated`` его не воскресит.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from gv_core.domain import Entity


class CollectionStatus(StrEnum):
    """Статус сборника для media-pipeline."""

    PUBLISHED = "published"
    HIDDEN = "hidden"
    DELETED = "deleted"


class CollectionState(Entity[UUID]):
    """Проекция сборника.

    Attributes:
        version: Версия последнего применённого события сборника.
        status: ``published`` — наборы своих загадок нужны; ``hidden``/``deleted`` — нет.
        updated_at: Когда проекция менялась.
    """

    def __init__(
        self,
        id: UUID,  # noqa: A002
        *,
        version: int,
        status: CollectionStatus,
        updated_at: datetime,
    ) -> None:
        super().__init__(id)
        self.version = version
        self.status = status
        self.updated_at = updated_at

    @property
    def wants_assets(self) -> bool:
        """Сборник играется — наборы его своих загадок нужны."""
        return self.status is CollectionStatus.PUBLISHED
