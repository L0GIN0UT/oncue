"""Страница лидербордов (план этапа 4, §8; ADR 0037, 0039).

Публичная: без токена показывает общий лидерборд и топ недели (кэшируются на секунды).
Токен пользователя уходит в rating только при ``around=me`` — тогда страница личная
(строка ``me``), не кэшируется. Блоки гаснут независимо.
"""

from __future__ import annotations

import asyncio

import structlog

from bff_web_service.application.dto import (
    LadderCard,
    LeaderboardCard,
    LeaderboardsBlocks,
    LeaderboardsPage,
    SubjectKind,
    Viewer,
    WeeklyTopCard,
)
from bff_web_service.application.ports import RatingClient, UpstreamRejectedError
from bff_web_service.application.queries.common import (
    HTTP_NOT_FOUND,
    Limiter,
    UpstreamError,
    block_of,
    screen_error,
)
from bff_web_service.domain import Block
from gv_core.errors import NotFoundError, UnauthenticatedError

log = structlog.get_logger(__name__)

LADDER_NOT_FOUND = "rating.ladder_not_found"
NOT_RANKED = "rating.not_ranked"


def user_token(viewer: Viewer) -> str | None:
    """Токен пользователя для личных вызовов; гость, аноним и отвергнутый токен — ``None``."""
    if viewer.token is None or viewer.token_rejected or viewer.kind is SubjectKind.GUEST:
        return None
    return viewer.token


class GetLeaderboardsPage:
    """Лидерборд ladder-а, топ недели и список ladder-ов для переключателя."""

    def __init__(self, rating: RatingClient, *, concurrency: int) -> None:
        self._rating, self._concurrency = rating, concurrency

    async def __call__(
        self, viewer: Viewer, ladder: str | None, cursor: str | None, *, around_me: bool
    ) -> LeaderboardsPage:
        """Собрать страницу.

        Args:
            viewer: Зритель; его токен используется только при ``around_me``.
            ladder: Ключ ladder-а; ``None`` — первый из списка rating.
            cursor: Курсор лидерборда от rating.
            around_me: Окно вокруг пользователя (нужен вход).

        Raises:
            UnauthenticatedError: ``around_me`` без входа пользователя.
            NotFoundError: ``rating.ladder_not_found``.
            InvalidInputError: rating отклонил курсор (422).
        """
        token = user_token(viewer)
        if around_me and token is None:
            raise UnauthenticatedError("around=me требует входа")
        personal_token = token if around_me else None
        limiter = Limiter(self._concurrency)
        board: LeaderboardCard | UpstreamError | None
        weekly: WeeklyTopCard | UpstreamError | None
        if ladder is not None:
            ladders, board, weekly = await asyncio.gather(
                limiter.attempt(self._rating.ladders()),
                self._board(limiter, ladder, personal_token, cursor, around_me=around_me),
                limiter.attempt(self._rating.weekly(ladder, token=personal_token)),
            )
        else:
            ladders = await limiter.attempt(self._rating.ladders())
            key = _first_key(ladders)
            if key is None:
                board = weekly = None  # ключа нет: блоки гаснут, причина видна в `ladders`
            else:
                ladder = key
                board, weekly = await asyncio.gather(
                    self._board(limiter, key, personal_token, cursor, around_me=around_me),
                    limiter.attempt(self._rating.weekly(key, token=personal_token)),
                )
        self._check_ladder(ladder, ladders, board, weekly)
        return LeaderboardsPage(
            blocks=LeaderboardsBlocks(
                ladders=block_of(ladders, "leaderboards.ladders"),
                leaderboard=_block(board, "leaderboards.leaderboard"),
                weekly=_block(weekly, "leaderboards.weekly"),
            ),
            personal=personal_token is not None,
        )

    async def _board(
        self,
        limiter: Limiter,
        ladder: str,
        token: str | None,
        cursor: str | None,
        *,
        around_me: bool,
    ) -> LeaderboardCard | UpstreamError:
        """Лидерборд; ``around=me`` в калибровке — обычная первая страница."""
        result = await limiter.attempt(
            self._rating.leaderboard(ladder, token=token, cursor=cursor, around_me=around_me)
        )
        if around_me and isinstance(result, UpstreamRejectedError) and result.code == NOT_RANKED:
            return await limiter.attempt(
                self._rating.leaderboard(ladder, token=token, cursor=cursor, around_me=False)
            )
        return result

    @staticmethod
    def _check_ladder(
        ladder: str | None,
        ladders: list[LadderCard] | UpstreamError | None,
        *values: LeaderboardCard | WeeklyTopCard | UpstreamError | None,
    ) -> None:
        """Нет такого ladder-а — 404 экрана; прочие отказы источника → ответ экрана или блок."""
        if (
            ladder is not None
            and isinstance(ladders, list)
            and not any(x.key == ladder for x in ladders)
        ):
            raise NotFoundError("ladder не найден", code=LADDER_NOT_FOUND)
        for value in values:
            if not isinstance(value, UpstreamRejectedError):
                continue
            error = screen_error(value, not_found="ladder не найден")
            if error is not None:
                if value.status == HTTP_NOT_FOUND and value.code != LADDER_NOT_FOUND:
                    continue
                raise error from value


def _first_key(ladders: list[LadderCard] | UpstreamError) -> str | None:
    if isinstance(ladders, list) and ladders:
        return ladders[0].key
    return None


def _block[T](value: T | UpstreamError | None, name: str) -> Block[T]:
    """Блок; ``None`` — вызов не делался (нет ladder-а для запроса): ``unavailable``."""
    if value is None:
        return Block.unavailable()
    return block_of(value, name)
