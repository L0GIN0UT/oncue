"""Проекция профилей identity: ники для лидербордов, скрытие удалённых аккаунтов."""

from __future__ import annotations

from uuid import UUID

from rating_service.application.commands.leaderboards import Leaderboards
from rating_service.application.ports import RatingUnitOfWork


class UserDirectory:
    """Применение событий identity к проекции ``users_proj``."""

    def __init__(self, boards: Leaderboards) -> None:
        self._boards = boards

    @staticmethod
    async def profile(uow: RatingUnitOfWork, user_id: UUID, nickname: str, version: int) -> None:
        """``UserRegistered`` / ``UserProfileBasicsChanged``."""
        await uow.users.profile(user_id, nickname, version)

    async def forget(self, uow: RatingUnitOfWork, user_id: UUID, version: int) -> None:
        """``UserDeleted``: ник стёрт, игрок исчезает из лидербордов."""
        await uow.users.forget(user_id, version)
        await self._boards.forget_user(user_id)
