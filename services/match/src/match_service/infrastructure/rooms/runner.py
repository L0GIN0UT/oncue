"""Процесс ``rooms``: владельцы комнат (D6, D7, ADR 0034).

``RoomsRunner`` — ``gv_service.Runner``: берёт комнаты без владельца (сигнал
``rooms:orphans`` и периодический обход ``rooms:active`` на истёкшие lease), не больше
``GV_MATCH__MAX_ROOMS_PER_INSTANCE``, и на каждую запускает ``RoomActor``.

Цикл ``RoomActor`` (один писатель на комнату):

1. ``XREAD`` команд после курсора снимка (ожидание — до дедлайна фазы, не дольше
   ``GV_MATCH__ROOMS_POLL_MS``);
2. ``RoomOwner.handle`` каждой команды, затем ``RoomOwner.tick`` (дедлайны, присутствие);
3. если состояние изменилось — Lua-CAS снимка с эпохой; отказ = lease потерян: комната
   брошена без ответов (их даст новый владелец, применив команды заново с курсора);
4. публикация сообщений канала, ``XDEL`` применённых, ответы отправителям, индексы;
5. продление lease каждые ``GV_MATCH__LEASE_RENEW_S``.

Handoff по SIGTERM: актёры выходят на границе итерации (состояние = последний снимок),
затем lease отпускается и код уходит в ``rooms:orphans`` — другой инстанс подхватывает
комнату без ожидания TTL; непримененные команды остаются в потоке.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass

import structlog
from prometheus_client import Counter, Gauge
from redis.asyncio import Redis
from redis.exceptions import RedisError

from gv_core.clock import Clock
from gv_core.errors import AppError
from match_service.application.owner import RoomOwner
from match_service.application.ports import RealtimePublisher
from match_service.application.views import messages
from match_service.domain.model import Change
from match_service.domain.room import Room
from match_service.infrastructure.rooms import keys
from match_service.infrastructure.rooms.codec import dumps
from match_service.infrastructure.rooms.lease import Grant, Lease
from match_service.infrastructure.rooms.snapshot import RedisRoomStore
from match_service.infrastructure.rooms.stream import CommandStream, RedisPresence

log = structlog.get_logger(__name__)

ROOMS_ACTIVE = Gauge("gv_match_rooms_active", "Комнаты, которыми владеет инстанс rooms")
LEASE_TAKEOVERS = Counter(
    "gv_match_lease_takeovers_total",
    "Захваты комнат: orphan — отдана при handoff или новая, expired — lease истёк",
    ["reason"],
)
LEASE_LOST = Counter("gv_match_lease_lost_total", "Комнаты, брошенные из-за потери lease")


@dataclass(frozen=True, slots=True)
class RunnerSettings:
    """Параметры процесса ``rooms`` (из ``GV_MATCH__*``)."""

    instance: str
    lease_ttl_ms: int
    renew_ms: int
    poll_ms: int
    max_rooms: int
    scan_ms: int
    snapshot_ttl_ms: int
    closed_ttl_s: int
    reply_ttl_s: int
    presence_ttl_s: int
    batch: int = 50


def _channel(code: str) -> str:
    return f"room:{code}"


class RoomActor:
    """Владелец одной комнаты."""

    def __init__(
        self,
        grant: Grant,
        *,
        lease: Lease,
        store: RedisRoomStore,
        stream: CommandStream,
        presence: RedisPresence,
        owner: RoomOwner,
        publisher: RealtimePublisher,
        clock: Clock,
        settings: RunnerSettings,
    ) -> None:
        self.grant = grant
        self._lease = lease
        self._store = store
        self._stream = stream
        self._presence = presence
        self._owner = owner
        self._publisher = publisher
        self._clock = clock
        self._settings = settings
        self.lost = False
        self.stopped = False
        self._room: Room | None = None
        self._written: tuple[object, ...] = ()
        self._renewed = 0.0

    @property
    def code(self) -> str:
        """Код комнаты."""
        return self.grant.code

    def _now_ms(self) -> int:
        return int(self._clock.now().timestamp() * 1000)

    def _block_ms(self, room: Room, renew_left_ms: float) -> int:
        block = float(self._settings.poll_ms)
        if room.phase_ends_at_ms is not None:
            block = min(block, max(1, room.phase_ends_at_ms - self._now_ms()))
        return int(max(1, min(block, renew_left_ms)))

    @staticmethod
    def _signature(room: Room) -> tuple[object, ...]:
        match = room.match
        return (
            room.version,
            room.cursor,
            match.started_written if match else None,
            match.finished_written if match else None,
            match.final_clue if match else None,
        )

    async def run(self, stop: asyncio.Event) -> None:
        """Владеть комнатой до сигнала, закрытия или потери lease."""
        try:
            await self._run(stop)
        finally:
            self.stopped = stop.is_set() and not self.lost
            if self._room is not None:
                self._owner.forget(self._room)

    async def _run(self, stop: asyncio.Event) -> None:
        room = await self._store.load(self.code)
        if room is None:
            await self._lease.release(self.grant)
            return
        self._room = room
        self._written = self._signature(room)
        self._renewed = time.monotonic()
        while not stop.is_set():
            if room.is_closed:
                await self._close(room)
                return
            if not await self._iteration(room, stop):
                return

    async def _iteration(self, room: Room, stop: asyncio.Event) -> bool:
        """Одна итерация цикла; ``False`` — выйти (остановка или потеря lease)."""
        renew_left = self._settings.renew_ms - (time.monotonic() - self._renewed) * 1000
        entries = await self._stream.read(
            self.code,
            room.cursor,
            block_ms=self._block_ms(room, renew_left),
            count=self._settings.batch,
        )
        if stop.is_set():
            return False  # команды останутся в потоке: их применит следующий владелец
        replies = []
        for entry_id, command in entries:
            if command is not None:
                replies.append((command, await self._owner.handle(room, command)))
            else:
                log.warning("match.command_unreadable", room=self.code)
            room.cursor = entry_id
        await self._owner.tick(room, await self._presence.seen(self.code))
        changes = room.pull_changes()
        signature = self._signature(room)
        if signature != self._written:
            if not await self._lease.write(self.grant, dumps(room), self._settings.snapshot_ttl_ms):
                self._lose("snapshot")
                return False
            self._written = signature
            await self._publish(room, changes)
            await self._stream.ack(self.code, [e for e, _ in entries])
            for command, reply in replies:
                await self._stream.reply(self.code, command, reply)
            await self._store.index(room)
        if (time.monotonic() - self._renewed) * 1000 >= self._settings.renew_ms:
            if not await self._lease.renew(self.grant):
                self._lose("renew")
                return False
            self._renewed = time.monotonic()
        return True

    async def _publish(self, room: Room, changes: list[Change]) -> None:
        """Сообщения канала; отказ Centrifugo не мешает матчу (клиенты — на HTTP-tick)."""
        for message in messages(room, changes, self._now_ms()):
            try:
                await self._publisher.publish(_channel(self.code), message)
            except AppError as exc:
                log.warning("match.realtime_publish_failed", room=self.code, code=exc.code)
                return

    def _lose(self, stage: str) -> None:
        self.lost = True
        LEASE_LOST.inc()
        log.warning("match.lease_lost", room=self.code, epoch=self.grant.epoch, stage=stage)

    async def _close(self, room: Room) -> None:
        await self._store.index(room)
        await self._store.expire_closed(room, self._settings.closed_ttl_s)
        await self._lease.release(self.grant)
        log.info("match.room_closed", room=self.code, reason=room.close_reason)

    async def handoff(self) -> None:
        """Отдать комнату: lease — свободен, код — в ``rooms:orphans``."""
        if self.lost or self._room is None or self._room.is_closed:
            return
        if await self._lease.release(self.grant):
            await self._store.signal_orphan(self.code)


class RoomsRunner:
    """``Runner`` процесса ``rooms`` match."""

    def __init__(
        self,
        redis: Redis,
        *,
        owner: RoomOwner,
        publisher: RealtimePublisher,
        store: RedisRoomStore,
        clock: Clock,
        settings: RunnerSettings,
    ) -> None:
        self._redis = redis
        self._owner = owner
        self._publisher = publisher
        self._store = store
        self._clock = clock
        self._settings = settings
        self.lease = Lease(redis, settings.instance, settings.lease_ttl_ms)
        self._stream = CommandStream(redis, reply_ttl_s=settings.reply_ttl_s)
        self._presence = RedisPresence(redis, ttl_s=settings.presence_ttl_s)
        self.actors: dict[str, RoomActor] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def name(self) -> str:
        """Имя runner-а."""
        return "match.rooms"

    async def run(self, stop: asyncio.Event) -> None:
        """Брать комнаты до сигнала остановки; затем дождаться выхода актёров."""
        last_scan = 0.0
        try:
            while not stop.is_set():
                self._reap()
                try:
                    if time.monotonic() - last_scan >= self._settings.scan_ms / 1000:
                        await self._scan(stop)
                        last_scan = time.monotonic()
                    await self._take_orphan(stop)
                except (RedisError, OSError) as exc:
                    log.warning("match.rooms_redis_error", error_type=type(exc).__name__)
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), self._settings.poll_ms / 1000)
        finally:
            if not stop.is_set():
                # Отмена (сбой процесса): актёры не доделывают итерацию, lease истечёт сам.
                for task in self._tasks.values():
                    task.cancel()
            if self._tasks:
                await asyncio.gather(*self._tasks.values(), return_exceptions=True)
            self._reap()

    async def handoff(self) -> None:
        """Отдать все комнаты другим инстансам."""
        actors = list(self.actors.values())
        await asyncio.gather(*(a.handoff() for a in actors), return_exceptions=True)
        self.actors.clear()
        ROOMS_ACTIVE.set(0)

    def _reap(self) -> None:
        for code, task in list(self._tasks.items()):
            if not task.done():
                continue
            del self._tasks[code]
            actor = self.actors.get(code)
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                log.error(
                    "match.room_actor_failed",
                    room=code,
                    error_type=type(exc).__name__,
                    exc_info=exc,
                )
            # Остановленные по сигналу остаются до handoff; закрытые, брошенные и упавшие —
            # уходят (lease упавшего истечёт, комнату заберёт обход).
            if actor is not None and (exc is not None or not actor.stopped):
                self.actors.pop(code, None)
        ROOMS_ACTIVE.set(len(self.actors))

    def _has_capacity(self) -> bool:
        return len(self.actors) < self._settings.max_rooms

    async def _take_orphan(self, stop: asyncio.Event) -> None:
        if not self._has_capacity():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), self._settings.poll_ms / 1000)
            return
        item = await self._redis.blpop([keys.ORPHANS], timeout=self._settings.poll_ms / 1000)
        if item is None or stop.is_set():
            if item is not None:  # остановка: вернуть сигнал другим инстансам
                await self._redis.rpush(keys.ORPHANS, item[1])
            return
        await self._claim(_text(item[1]), "orphan", stop)

    async def _scan(self, stop: asyncio.Event) -> None:
        """Комнаты без владельца (упавший инстанс): захват после истечения lease."""
        async for raw, _ in self._redis.zscan_iter(keys.ACTIVE):
            if stop.is_set() or not self._has_capacity():
                return
            code = _text(raw)
            if code in self.actors:
                continue
            if not await self._redis.exists(keys.owner(code)):
                await self._claim(code, "expired", stop)

    async def _claim(self, code: str, reason: str, stop: asyncio.Event) -> None:
        if code in self.actors:
            return
        grant = await self.lease.acquire(code)
        if grant is None:
            return
        if grant.epoch > 1:
            LEASE_TAKEOVERS.labels(reason).inc()
        actor = RoomActor(
            grant,
            lease=self.lease,
            store=self._store,
            stream=self._stream,
            presence=self._presence,
            owner=self._owner,
            publisher=self._publisher,
            clock=self._clock,
            settings=self._settings,
        )
        self.actors[code] = actor
        self._tasks[code] = asyncio.create_task(actor.run(stop), name=f"room:{code}")
        ROOMS_ACTIVE.set(len(self.actors))
        log.info("match.room_acquired", room=code, epoch=grant.epoch, reason=reason)


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
