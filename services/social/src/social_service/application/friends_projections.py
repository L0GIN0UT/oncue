"""Проекции из событий других сервисов и удаление аккаунта (ADR 0050).

Идемпотентность двойная: inbox по ``event_id`` (в процессоре) и сам эффект - ник пишется
только при большей ``version``, «в матче» - upsert по ``(user_id, match_id)``, удаление
аккаунта повторно ничего не находит.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from gv_core.clock import Clock
from social_service.application.ports.friends import FriendsUowFactory
from social_service.domain.friend_events import FriendRemoved


class FriendsProjectionService:
    """Применение событий identity и match."""

    def __init__(self, uow: FriendsUowFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def apply_nickname(self, user_id: UUID, nickname: str, version: int) -> bool:
        """Записать ник; ``False`` - версия не новее применённой (отброшено)."""
        async with self._uow() as uow:
            applied = await uow.repo.upsert_user(user_id, nickname, version, self._clock.now())
            await uow.commit()
        return applied

    async def match_started(self, match_id: UUID, user_ids: Sequence[UUID]) -> None:
        """Игроки матча - «в матче»."""
        async with self._uow() as uow:
            await uow.repo.match_started(match_id, user_ids, self._clock.now())
            await uow.commit()

    async def match_finished(self, match_id: UUID) -> None:
        """Матч завершён - отметки сняты."""
        async with self._uow() as uow:
            await uow.repo.match_finished(match_id)
            await uow.commit()

    async def user_deleted(self, user_id: UUID) -> int:
        """Стереть данные игрока: дружбы (с ``FriendRemoved``), заявки, код, блокировки.

        Returns:
            Сколько дружб разорвано.
        """
        now = self._clock.now()
        async with self._uow() as uow:
            removed = await uow.repo.erase_user(user_id)
            for friendship in removed:
                uow.publish(
                    FriendRemoved(
                        aggregate_id=str(friendship.user_a),
                        user_a=friendship.user_a,
                        user_b=friendship.user_b,
                        removed_by=user_id,
                        reason="account_deleted",
                        at=now,
                    )
                )
            await uow.commit()
        return len(removed)
