"""Чтение комнат без владельца (R9, D9, D10): снимок из Redis, фильтр по зрителю.

``state`` и ``tick`` читают снимок напрямую (владелец может быть занят или сменяться) и
строят виды ``application.views`` — личное только в ``me`` самого зрителя.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from gv_core.clock import Clock
from match_service.application.commands.rooms import Actor
from match_service.application.ports import (
    Json,
    PresenceStore,
    RateLimiter,
    RoomStore,
    SubscriptionTokens,
)
from match_service.application.views import iso, mode_ref, room_state, room_view, settings_json
from match_service.domain import errors
from match_service.domain.errors import (
    MatchForbiddenError,
    MatchInvalidError,
    MatchNotFoundError,
)
from match_service.domain.model import Presence
from match_service.domain.room import Room


def encode_cursor(*parts: object) -> str:
    """Непрозрачный курсор (base64url без паддинга)."""
    raw = "|".join(str(p) for p in parts).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> list[str] | None:
    """Части курсора или ``None``; повреждённый курсор — 422 ``match.cursor_invalid``."""
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    except (ValueError, UnicodeDecodeError) as exc:
        raise _bad_cursor() from exc
    return raw.split("|")


def _bad_cursor() -> MatchInvalidError:
    return MatchInvalidError("неверный курсор", code="match.cursor_invalid")


@dataclass(frozen=True, slots=True)
class QueryLimits:
    """Лимиты чтений (``GV_MATCH__*``)."""

    join_by_code_per_ip_per_min: int
    tokens_per_min: int


class RoomQueries:
    """Комната по коду, снимок, tick, realtime-токен, открытые лобби, админ-список."""

    def __init__(
        self,
        *,
        store: RoomStore,
        presence: PresenceStore,
        tokens: SubscriptionTokens,
        limiter: RateLimiter,
        clock: Clock,
        limits: QueryLimits,
    ) -> None:
        self._store = store
        self._presence = presence
        self._tokens = tokens
        self._limiter = limiter
        self._clock = clock
        self._limits = limits

    def _now(self) -> datetime:
        return self._clock.now()

    def _now_ms(self) -> int:
        return int(self._now().timestamp() * 1000)

    async def _room(self, code: str) -> Room:
        room = await self._store.load(code)
        if room is None or room.is_closed:
            raise MatchNotFoundError("комнаты нет или она закрыта", code=errors.ROOM_NOT_FOUND)
        return room

    async def _participant(self, code: str, user_id: UUID) -> Room:
        room = await self._room(code)
        seat = room.seat_of(user_id)
        if seat is None or seat.presence is Presence.LEFT:
            raise MatchForbiddenError("вы не участник комнаты", code=errors.NOT_PARTICIPANT)
        return room

    async def get(self, actor: Actor, code: str) -> Json:
        """Комната по коду (экран входа): без данных матча; перебор кодов — fail-closed."""
        await self._limiter.hit(
            "join_ip",
            actor.ip or "unknown",
            limit=self._limits.join_by_code_per_ip_per_min,
            window_s=60,
            fail_closed=True,
        )
        return room_view(await self._room(code), self._now_ms())

    async def state(self, user_id: UUID, code: str) -> Json:
        """Полный снимок для участника."""
        room = await self._participant(code, user_id)
        return room_state(room, user_id, self._now_ms())

    async def tick(self, user_id: UUID, code: str, known_version: int | None) -> Json:
        """Heartbeat присутствия и снимок, если версия новее известной клиенту."""
        room = await self._participant(code, user_id)
        now = self._now_ms()
        await self._presence.touch(code, user_id, now)
        fresh = known_version is None or room.version > known_version
        return {
            "version": room.version,
            "server_now": iso(now),
            "state": room_state(room, user_id, now) if fresh else None,
        }

    async def realtime_token(self, user_id: UUID, code: str) -> Json:
        """Subscription-токен канала ``room:{code}`` — только участнику (D9)."""
        await self._limiter.hit(
            "rt_token", str(user_id), limit=self._limits.tokens_per_min, window_s=60
        )
        room = await self._participant(code, user_id)
        channel = f"room:{room.code}"
        token, expires = self._tokens.issue(user_id, channel, self._now())
        return {"channel": channel, "token": token, "expires_at": expires.isoformat()}

    async def open_rooms(self, cursor: str | None, limit: int) -> Json:
        """Открытые лобби (keyset по времени создания и коду)."""
        parts = decode_cursor(cursor)
        position: tuple[int, str] | None = None
        if parts is not None:
            try:
                position = (int(parts[0]), parts[1])
            except (IndexError, ValueError) as exc:
                raise _bad_cursor() from exc
        rooms, nxt = await self._store.open_rooms(position, limit)
        items = []
        for room in rooms:
            host = room.seat_of(room.host_id)
            items.append(
                {
                    "code": room.code,
                    "mode": mode_ref(room),
                    "host_nickname": host.nickname if host is not None else "",
                    "players": sum(1 for s in room.seats if s.presence is not Presence.LEFT),
                    "players_max": room.settings.players_max,
                    "settings": settings_json(room),
                    "created_at": iso(room.created_at_ms),
                }
            )
        return {"items": items, "next_cursor": encode_cursor(*nxt) if nxt else None}

    async def admin_rooms(self, phase: str | None, cursor: str | None, limit: int) -> Json:
        """Активные комнаты для админки (фильтр по фазе — на странице)."""
        rooms, nxt = await self._store.active_rooms(cursor, limit)
        items = [
            {
                "code": room.code,
                "mode": mode_ref(room),
                "visibility": room.settings.visibility,
                "phase": room.phase.value,
                "host_id": str(room.host_id),
                "players": sum(1 for s in room.seats if s.presence is not Presence.LEFT),
                "match_id": str(room.match.match_id) if room.match else None,
                "created_at": iso(room.created_at_ms),
                "owner_epoch": epoch,
            }
            for room, epoch in rooms
            if not room.is_closed and (phase is None or room.phase.value == phase)
        ]
        return {"items": items, "next_cursor": nxt}
