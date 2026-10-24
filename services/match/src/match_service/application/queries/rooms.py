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
from match_service.application.commands.rooms import Actor, code_lookup_limits
from match_service.application.ports import (
    Json,
    PresenceStore,
    RateLimiter,
    RoomStore,
    SpectatorRegistry,
    SubscriptionTokens,
)
from match_service.application.sanctions import SanctionGate
from match_service.application.views import iso, mode_ref, room_state, room_view, settings_json
from match_service.domain import errors
from match_service.domain.errors import (
    MatchForbiddenError,
    MatchInvalidError,
    MatchNotFoundError,
)
from match_service.domain.model import Phase, Presence
from match_service.domain.room import Room

WATCHED_PHASES = frozenset({Phase.COUNTDOWN, Phase.LEVEL_OPEN, Phase.REVEAL, Phase.PAUSED})
"""Фазы идущего матча, которые видит наблюдатель."""


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
    join_by_code_per_user_per_min: int = 20
    tick_per_min: int = 150
    state_per_min: int = 60
    spectate_per_min: int = 60
    spectate_scan_pages: int = 10
    window_s: int = 60
    spectator_ttl_s: int = 3600


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
        spectators: SpectatorRegistry | None = None,
        sanctions: SanctionGate | None = None,
    ) -> None:
        self._sanctions = sanctions
        self._store = store
        self._presence = presence
        self._tokens = tokens
        self._limiter = limiter
        self._clock = clock
        self._limits = limits
        self._spectators = spectators

    def _now(self) -> datetime:
        return self._clock.now()

    def _now_ms(self) -> int:
        return int(self._now().timestamp() * 1000)

    async def _room(self, code: str) -> Room:
        room = await self._store.load(code)
        if room is None or room.is_closed:
            raise MatchNotFoundError("комнаты нет или она закрыта", code=errors.ROOM_NOT_FOUND)
        return room

    async def _participant(self, code: str, user_id: UUID, *, closed: bool = False) -> Room:
        """Участник комнаты; ``closed`` — закрытая комната ещё читается (код реванша)."""
        room = await self._store.load(code) if closed else await self._room(code)
        if room is None:
            raise MatchNotFoundError("комнаты нет или она закрыта", code=errors.ROOM_NOT_FOUND)
        seat = room.seat_of(user_id)
        if seat is None or seat.presence is Presence.LEFT:
            raise MatchForbiddenError("вы не участник комнаты", code=errors.NOT_PARTICIPANT)
        return room

    @staticmethod
    def _watchable(room: Room) -> bool:
        """Идущий матч (отсчёт, уровни, ревил, пауза): лобби и итог наблюдателю не показываются."""
        return (
            not room.is_closed
            and room.match is not None
            and room.match.finish is None
            and room.phase in WATCHED_PHASES
        )

    async def _viewer(
        self, code: str, user_id: UUID, *, spectator: bool, closed: bool
    ) -> tuple[Room, bool]:
        """Участник комнаты, а при праве ``match.spectate`` — наблюдатель идущего матча.

        Returns:
            ``(комната, это_наблюдатель)``. Запросы наблюдателя делят лимит ``spectate``.
        """
        try:
            return await self._participant(code, user_id, closed=closed), False
        except MatchForbiddenError:
            if not spectator:
                raise
        await self._limiter.hit(
            "spectate",
            str(user_id),
            limit=self._limits.spectate_per_min,
            window_s=self._limits.window_s,
        )
        room = await self._room(code)
        if not self._watchable(room):
            raise MatchNotFoundError("комнаты нет или она закрыта", code=errors.ROOM_NOT_FOUND)
        return room, True

    async def get(self, actor: Actor, code: str) -> Json:
        """Комната по коду (экран входа): без данных матча; перебор кодов — fail-closed."""
        await code_lookup_limits(
            self._limiter,
            actor,
            per_ip=self._limits.join_by_code_per_ip_per_min,
            per_user=self._limits.join_by_code_per_user_per_min,
        )
        return room_view(await self._room(code), self._now_ms())

    async def state(self, user_id: UUID, code: str, *, spectator: bool = False) -> Json:
        """Полный снимок для участника (лимит ``GV_MATCH__STATE_PER_PLAYER_PER_MIN``).

        Наблюдатель (``spectator``) получает тот же вид, что участник, но без ``me``: личные
        попытки и варианты ``pick`` строятся только для игрока матча.
        """
        await self._limiter.hit(
            "state", str(user_id), limit=self._limits.state_per_min, window_s=60
        )
        room, _ = await self._viewer(code, user_id, spectator=spectator, closed=True)
        return room_state(room, user_id, self._now_ms())

    async def tick(self, user_id: UUID, code: str, known_version: int | None) -> Json:
        """Heartbeat присутствия и снимок, если версия новее известной клиенту."""
        await self._limiter.hit("tick", str(user_id), limit=self._limits.tick_per_min, window_s=60)
        room = await self._participant(code, user_id, closed=True)
        now = self._now_ms()
        if not room.is_closed:
            await self._presence.touch(code, user_id, now)
        fresh = known_version is None or room.version > known_version
        return {
            "version": room.version,
            "server_now": iso(now),
            "state": room_state(room, user_id, now) if fresh else None,
        }

    async def realtime_token(
        self, user_id: UUID, code: str, *, spectator: bool = False, claim_sanctions: int = 0
    ) -> Json:
        """Subscription-токен канала ``room:{code}`` — участнику и наблюдателю (D9, D19).

        Канал публикует одни и те же дельты всем подписчикам (личное в него не попадает),
        публикации клиентов запрещены — наблюдатель только читает. Выдача и продление токена
        перепроверяют санкцию ``ONLINE`` (ADR 0052): отзыв подписки Centrifugo может не дойти,
        а новый токен уже не выдаётся (хвост 4a).

        Raises:
            MatchForbiddenError: ``match.online_banned``.
        """
        if self._sanctions is not None:
            await self._sanctions.require_online(user_id, claim_sanctions)
        await self._limiter.hit(
            "rt_token", str(user_id), limit=self._limits.tokens_per_min, window_s=60
        )
        room, watching = await self._viewer(code, user_id, spectator=spectator, closed=False)
        if watching and self._spectators is not None:
            # Запомнить наблюдателя: по концу матча владелец отзовёт его подписку.
            await self._spectators.add(room.code, user_id, self._limits.spectator_ttl_s)
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

    async def spectate_rooms(self, user_id: UUID, cursor: str | None, limit: int) -> Json:
        """Идущие матчи для наблюдения (право ``match.spectate``), новые первыми.

        Просматривает не больше ``GV_MATCH__SPECTATE_SCAN_PAGES`` страниц индекса за запрос
        и возвращает первую непустую; ``next_cursor`` продолжает обход. Лобби и комнаты
        с итогом не показываются (код лобби — приватный).
        """
        await self._limiter.hit(
            "spectate",
            str(user_id),
            limit=self._limits.spectate_per_min,
            window_s=self._limits.window_s,
        )
        position = cursor
        if cursor is not None and not cursor.isdigit():
            raise _bad_cursor()
        for _ in range(self._limits.spectate_scan_pages):
            rooms, position = await self._store.active_rooms(position, limit)
            items = [
                {
                    "code": room.code,
                    "mode": mode_ref(room),
                    "phase": room.phase.value,
                    "players": sum(1 for s in room.seats if s.presence is not Presence.LEFT),
                    "started_at": iso(
                        room.match.started_at_ms or room.match.created_at_ms
                        if room.match is not None
                        else room.created_at_ms
                    ),
                }
                for room, _epoch in rooms
                if self._watchable(room)
            ]
            if items or position is None:
                return {"items": items, "next_cursor": position}
        return {"items": [], "next_cursor": position}

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
