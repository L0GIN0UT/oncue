"""Экраны прогрессии (план этапа 5, §7; D1–D8): профиль и свёртка прогресса для главной.

Профиль — только у пользователя: аноним получает 401, гость — 403 ``auth.guest_forbidden``,
progression при этом не вызывается. Блоки гаснут независимо; отказ токена (401/403) источником —
ответ экрана. Тексты заданий, бейджей и секторов приходят из progression, здесь их нет.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog

from bff_web_service.application.dto import (
    HomeProgressCard,
    PlayerBlocks,
    PlayerPage,
    ProfileBlocks,
    ProfilePage,
    ProgressCard,
    QuestsCard,
    SubjectKind,
    Viewer,
    WheelCard,
)
from bff_web_service.application.ports import ProgressionClient, UpstreamRejectedError
from bff_web_service.application.queries.common import (
    GUEST_FORBIDDEN,
    HTTP_FORBIDDEN,
    HTTP_UNAUTHORIZED,
    Limiter,
    block_of,
    screen_error,
)
from bff_web_service.application.queries.rating import user_token
from bff_web_service.domain import Block
from gv_core.errors import ForbiddenError, UnauthenticatedError

log = structlog.get_logger(__name__)


class GetProfilePage:
    """Профиль: уровень, Версы, задания, бейджи, колесо."""

    def __init__(self, progression: ProgressionClient, *, concurrency: int) -> None:
        self._progression, self._concurrency = progression, concurrency

    async def __call__(self, viewer: Viewer) -> ProfilePage:
        """Собрать страницу.

        Raises:
            UnauthenticatedError: аноним или progression не принял токен.
            ForbiddenError: ``auth.guest_forbidden`` — гость (или 403 источника).
        """
        if viewer.token is None or viewer.token_rejected:
            raise UnauthenticatedError("требуется вход")
        if viewer.kind is SubjectKind.GUEST:
            raise ForbiddenError("профиль доступен только пользователям", code=GUEST_FORBIDDEN)
        token, p = viewer.token, self._progression
        limiter = Limiter(self._concurrency)
        progress, wallet, quests, badges, wheel = await asyncio.gather(
            limiter.attempt(p.progress(token)),
            limiter.attempt(p.wallet(token)),
            limiter.attempt(p.quests(token)),
            limiter.attempt(p.badges(token)),
            limiter.attempt(p.wheel(token)),
        )
        for result in (progress, wallet, quests, badges, wheel):
            if isinstance(result, UpstreamRejectedError) and result.status in (
                HTTP_UNAUTHORIZED,
                HTTP_FORBIDDEN,
            ):
                error = screen_error(result, not_found="")
                if error is not None:
                    raise error
        return ProfilePage(
            blocks=ProfileBlocks(
                progress=block_of(progress, "profile.progress"),
                wallet=block_of(wallet, "profile.wallet"),
                quests=block_of(quests, "profile.quests"),
                badges=block_of(badges, "profile.badges"),
                wheel=block_of(wheel, "profile.wheel"),
            )
        )


class GetPlayerPage:
    """Профиль чужого игрока: публичный прогресс (уровень, опыт, бейджи). Только с аккаунтом."""

    def __init__(self, progression: ProgressionClient, *, concurrency: int) -> None:
        self._progression, self._concurrency = progression, concurrency

    async def __call__(self, viewer: Viewer, user_id: UUID) -> PlayerPage:
        """Собрать страницу.

        Raises:
            UnauthenticatedError: аноним или progression не принял токен.
            ForbiddenError: ``auth.guest_forbidden`` — гость.
            NotFoundError: игрока нет (404 progression).
        """
        if viewer.token is None or viewer.token_rejected:
            raise UnauthenticatedError("требуется вход")
        if viewer.kind is SubjectKind.GUEST:
            raise ForbiddenError(
                "профиль игрока доступен только пользователям", code=GUEST_FORBIDDEN
            )
        result = await Limiter(self._concurrency).attempt(
            self._progression.player(viewer.token, user_id)
        )
        if isinstance(result, UpstreamRejectedError) and (
            error := screen_error(result, not_found="игрок не найден")
        ):
            raise error
        return PlayerPage(blocks=PlayerBlocks(progress=block_of(result, "player.progress")))


class HomeProgress:
    """Блок ``progress`` главной: свёртка трёх источников progression под общим сроком."""

    def __init__(
        self, progression: ProgressionClient, *, concurrency: int, deadline_s: float
    ) -> None:
        self._progression = progression
        self._concurrency, self._deadline_s = concurrency, deadline_s

    async def __call__(self, viewer: Viewer) -> Block[HomeProgressCard] | None:
        """``None`` — гость или аноним (progression не вызывается); иначе блок."""
        token = user_token(viewer)
        if token is None:
            return None
        limiter = Limiter(self._concurrency)
        p = self._progression
        try:
            async with asyncio.timeout(self._deadline_s):
                progress, quests, wheel = await asyncio.gather(
                    limiter.attempt(p.progress(token)),
                    limiter.attempt(p.quests(token)),
                    limiter.attempt(p.wheel(token)),
                )
        except TimeoutError:
            log.warning("bff.block_unavailable", block="home.progress", reason="deadline")
            return Block.unavailable()
        if not (
            isinstance(progress, ProgressCard)
            and isinstance(quests, QuestsCard)
            and isinstance(wheel, WheelCard)
        ):
            for result in (progress, quests, wheel):
                block_of(result, "home.progress")  # причина сбоя — в лог
            return Block.unavailable()
        return Block.ok(
            HomeProgressCard(
                level=progress.level,
                xp_in_level=progress.xp_in_level,
                xp_to_next=progress.xp_to_next,
                best_streak=max((s.current for s in progress.streaks), default=0),
                can_spin=wheel.can_spin,
                quests_open=sum(1 for q in quests.daily if not q.completed),
            )
        )


__all__ = ["GetPlayerPage", "GetProfilePage", "HomeProgress"]
