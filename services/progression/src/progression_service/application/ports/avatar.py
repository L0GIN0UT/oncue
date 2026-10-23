"""Порты своей аватарки: заявки и действующий аватар (БД), UGC-клиент media-pipeline (ADR 0048)."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from progression_service.domain.avatar import AvatarRequest, CurrentAvatar, Moderation


@dataclass(frozen=True, slots=True)
class UgcUpload:
    """Ответ media-pipeline на приём файла: ассет в статусе ``pending``."""

    asset_id: UUID
    submitted_at: datetime


class MediaUgcClient(Protocol):
    """Передача файла в media-pipeline (internal, scope ``ugc.image``) - один вызов."""

    async def submit(
        self, *, owner_user_id: UUID, purpose: str, data: bytes, idempotency_key: str
    ) -> UgcUpload:
        """Отправить файл; ``Idempotency-Key`` пробрасывается как есть.

        Raises:
            AvatarInvalidImageError: файл отклонён (422).
            AvatarTooLargeError: файл больше лимита (413).
            AvatarUnavailableError: сеть, таймаут, 5xx и любой неожиданный ответ (503).
        """
        ...


class AvatarRepository(Protocol):
    """Заявки на аватарку и действующий аватар."""

    async def add_request(
        self, user_id: UUID, asset_id: UUID, submitted_at: datetime, request_id: UUID
    ) -> bool:
        """Заявка ``pending``; ``False`` - по ассету строка уже есть (повтор или решение раньше)."""
        ...

    async def lock_user(self, user_id: UUID) -> None:
        """Сериализовать заявки игрока до конца транзакции."""
        ...

    async def supersede_pending(self, user_id: UUID, keep_asset_id: UUID, now: datetime) -> int:
        """Прежние ``pending`` заявки игрока -> ``rejected`` с кодом ``superseded``."""
        ...

    async def latest_request(self, user_id: UUID) -> AvatarRequest | None:
        """Последняя заявка игрока (по времени подачи)."""
        ...

    async def request_by_asset(self, asset_id: UUID) -> AvatarRequest | None:
        """Заявка по ассету media-pipeline."""
        ...

    async def apply_decision(self, decision: Moderation) -> bool:
        """Записать решение, если его версия больше применённой по ассету (строку создаёт,
        если решение пришло раньше заявки). ``False`` - событие устарело или повторное."""
        ...

    async def set_current(self, decision: Moderation) -> bool:
        """Сделать ассет действующим, если он новее текущего (по ``approved_at``)."""
        ...

    async def clear_current(self, user_id: UUID, asset_id: UUID) -> bool:
        """Убрать аватар, если действующий - именно этот ассет."""
        ...

    async def current(self, user_id: UUID) -> CurrentAvatar | None:
        """Действующая аватарка игрока."""
        ...

    async def currents(self, user_ids: Collection[UUID]) -> dict[UUID, CurrentAvatar]:
        """Действующие аватарки нескольких игроков."""
        ...
