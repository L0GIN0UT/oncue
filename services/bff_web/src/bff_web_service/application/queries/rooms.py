"""Экран комнаты и общие помощники комнат: название и уровни игроков.

Название комнаты в match хранит только то, что ввёл хост; фразу «Комната <ник хоста>» для
комнаты без имени собирает bff по шаблону ``GV_PAGES__ROOM_NAME_TEMPLATE``. Уровни игроков
приходят из progression (internal-ручка, сервисный токен bff_web) после ответа match — ему
нужны id игроков; сбой progression не ломает страницу: ``level`` остаётся ``None``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID

import structlog

from bff_web_service.application.dto import (
    PlayerLevel,
    RoomBlocks,
    RoomLobby,
    RoomPage,
    SubjectKind,
    Viewer,
)
from bff_web_service.application.ports import (
    MatchClient,
    ProgressionClient,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from bff_web_service.application.queries.common import (
    GUEST_FORBIDDEN,
    Limiter,
    block_of,
    screen_error,
)
from bff_web_service.domain import Block
from gv_core.errors import ForbiddenError, UnauthenticatedError

log = structlog.get_logger(__name__)


def room_title(name: str | None, host_nickname: str, template: str) -> str:
    """Название для показа: введённое хостом или шаблон с ником хоста."""
    return name if name else template.replace("{host}", host_nickname)


async def fetch_levels(
    progression: ProgressionClient | None,
    limiter: Limiter,
    user_ids: Sequence[UUID],
    *,
    batch_size: int,
) -> dict[UUID, PlayerLevel]:
    """Уровни игроков пачками по ``batch_size``; сбой любой пачки даёт только её игроков без уровня.

    Дубли схлопываются. Клиент не подключён или список пуст — вызовов нет.
    """
    unique = list(dict.fromkeys(user_ids))
    if progression is None or not unique:
        return {}
    chunks = [unique[i : i + batch_size] for i in range(0, len(unique), batch_size)]
    fetched = await asyncio.gather(*(limiter.attempt(progression.levels(c)) for c in chunks))
    levels: dict[UUID, PlayerLevel] = {}
    for value in fetched:
        block = block_of(value, "rooms.levels")  # причина уходит в лог
        if block.data is not None:
            levels.update(block.data)
    return levels


class GetRoomPage:
    """Комната по коду: состав и настройки (match) + уровни игроков (progression)."""

    def __init__(
        self,
        match: MatchClient,
        progression: ProgressionClient,
        *,
        concurrency: int,
        room_name_template: str,
        levels_batch_size: int,
    ) -> None:
        self._match, self._progression = match, progression
        self._concurrency = concurrency
        self._template = room_name_template
        self._batch = levels_batch_size

    async def __call__(self, viewer: Viewer, code: str) -> RoomPage:
        """Собрать экран.

        Raises:
            UnauthenticatedError: аноним или match не принял токен.
            ForbiddenError: ``auth.guest_forbidden`` — гость (match не вызывается).
            NotFoundError: ``match.room_not_found``.

        Прочие 4xx match и его недоступность — блок ``room`` ``unavailable``, страница 200.
        """
        if viewer.token is None:
            raise UnauthenticatedError("требуется вход")
        if viewer.kind is SubjectKind.GUEST:
            raise ForbiddenError("онлайн доступен только пользователям", code=GUEST_FORBIDDEN)
        limiter = Limiter(self._concurrency)
        try:
            room = await limiter.run(self._match.room(viewer.token, code))
        except UpstreamUnavailableError as exc:
            log.warning("bff.block_unavailable", block="room", reason=exc.reason)
            return _unavailable()
        except UpstreamRejectedError as exc:
            error = screen_error(exc, not_found="комната не найдена")
            if error is not None:
                raise error from exc
            log.warning("bff.block_unavailable", block="room", reason=exc.code)
            return _unavailable()
        levels = await fetch_levels(
            self._progression,
            limiter,
            [p.user_id for p in room.players],
            batch_size=self._batch,
        )
        host = next((p.nickname for p in room.players if p.is_host), "")
        card = with_levels(room, levels, host_nickname=host, template=self._template)
        return RoomPage(RoomBlocks(room=Block.ok(card)))


def with_levels(
    room: RoomLobby, levels: dict[UUID, PlayerLevel], *, host_nickname: str, template: str
) -> RoomLobby:
    """Комната с названием для показа и уровнями игроков (кого нет в ``levels`` — без уровня)."""
    players = [
        replace(p, level=levels[p.user_id].level if p.user_id in levels else None)
        for p in room.players
    ]
    return replace(
        room,
        title=room_title(room.settings.name, host_nickname, template),
        players=players,
    )


def _unavailable() -> RoomPage:
    return RoomPage(RoomBlocks(room=Block.unavailable()))


__all__ = ["GetRoomPage", "fetch_levels", "room_title", "with_levels"]
