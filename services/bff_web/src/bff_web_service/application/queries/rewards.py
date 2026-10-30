"""Награды за игру на экране итогов (progression ``GET /v1/me/rewards``).

Начисление асинхронное: progression награждает по событию game, поэтому сразу после итога
записей может ещё не быть. Это не ошибка: блок ``ok`` с ``settled = false`` (клиент повторит
запрос позже), а bff до этого коротко переспрашивает — число попыток и пауза в настройках.
Сбой источника гасит только этот блок.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog

from bff_web_service.application.dto import RewardsCard, SessionRewardsCard, Viewer
from bff_web_service.application.ports import ProgressionClient
from bff_web_service.application.queries.common import Limiter, block_of
from bff_web_service.application.queries.rating import user_token
from bff_web_service.domain import Block

log = structlog.get_logger(__name__)


def rewards_card(value: SessionRewardsCard) -> RewardsCard:
    """Ответ progression → награды экрана."""
    leveled_up = (
        value.level_before is not None
        and value.level_after is not None
        and value.level_after > value.level_before
    )
    return RewardsCard(
        settled=value.settled,
        xp=value.xp,
        verses=value.verses,
        level_before=value.level_before,
        level_after=value.level_after,
        leveled_up=leveled_up,
        streak=value.streak,
    )


class ResultRewards:
    """Блок ``rewards`` итога: награды за сессию текущего пользователя."""

    def __init__(
        self,
        progression: ProgressionClient,
        *,
        concurrency: int,
        deadline_s: float,
        retries: int,
        retry_delay_s: float,
    ) -> None:
        self._progression = progression
        self._concurrency, self._deadline_s = concurrency, deadline_s
        self._retries, self._retry_delay_s = retries, retry_delay_s

    async def __call__(self, viewer: Viewer, session_id: UUID) -> Block[RewardsCard] | None:
        """``None`` — гость или аноним (progression не вызывается); иначе блок."""
        token = user_token(viewer)
        if token is None:
            return None
        limiter = Limiter(self._concurrency)
        try:
            async with asyncio.timeout(self._deadline_s):
                return await self._fetch(limiter, token, session_id)
        except TimeoutError:
            log.warning("bff.block_unavailable", block="results.rewards", reason="deadline")
            return Block.unavailable()

    async def _fetch(self, limiter: Limiter, token: str, session_id: UUID) -> Block[RewardsCard]:
        attempt = 0
        while True:
            value = await limiter.attempt(self._progression.session_rewards(token, session_id))
            if not isinstance(value, SessionRewardsCard):
                block_of(value, "results.rewards")  # причина уходит в лог
                return Block.unavailable()
            if value.settled or attempt >= self._retries:
                return Block.ok(rewards_card(value))
            attempt += 1
            await asyncio.sleep(self._retry_delay_s)
