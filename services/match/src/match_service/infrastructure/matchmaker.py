"""Runner матчера очередей (процесс ``rooms``, ADR 0038).

Каждая очередь ``(ladder, format)`` обслуживается одним матчером под lease с fencing-эпохой
(как комнаты, ADR 0034): lease берёт любой инстанс, продлевает на каждом проходе; потеря lease —
очередь бросается, а запись пары в Redis проверяет токен (поздний матчер пару не создаст).
Период прохода — ``matchmaking.interval_s`` из проекции ladder-а; пустая очередь — нормальное
состояние, проход стоит один ``ZCARD``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import structlog
from prometheus_client import Counter
from redis.asyncio import Redis
from redis.exceptions import RedisError

from match_service.application.matchmaking import Matchmaker
from match_service.application.ports import LadderProjection, QueueFence
from match_service.infrastructure.rooms import keys
from match_service.infrastructure.rooms.lease import Grant, Lease

log = structlog.get_logger(__name__)

PASS_ERRORS = Counter("gv_match_matchmaker_errors_total", "Сбои прохода матчера", ["kind"])


class MatchmakerRunner:
    """``Runner``: проходы матчера по всем очередям, которыми владеет инстанс."""

    def __init__(
        self,
        redis: Redis,
        *,
        matchmaker: Matchmaker,
        ladders: LadderProjection,
        instance: str,
        lease_ttl_ms: int,
        poll_ms: int,
    ) -> None:
        self._matchmaker = matchmaker
        self._ladders = ladders
        self._poll_ms = poll_ms
        self.lease = Lease(
            redis,
            instance,
            lease_ttl_ms,
            owner_key=keys.queue_owner,
            epoch_key=keys.queue_epoch,
            snapshot_key=keys.queue_unused,
        )
        self.held: dict[str, Grant] = {}
        self._next_at: dict[str, float] = {}

    @property
    def name(self) -> str:
        """Имя runner-а."""
        return "match.matchmaker"

    async def run(self, stop: asyncio.Event) -> None:
        """Работать до сигнала остановки."""
        while not stop.is_set():
            try:
                await self.pass_once()
            except (RedisError, OSError) as exc:
                PASS_ERRORS.labels("redis").inc()
                log.warning("match.matchmaker_redis_error", error_type=type(exc).__name__)
            except Exception:
                PASS_ERRORS.labels("bug").inc()
                log.exception("match.matchmaker_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), self._poll_ms / 1000)

    async def pass_once(self) -> int:
        """Один обход очередей: вернуть число созданных комнат (тесты зовут напрямую)."""
        created = 0
        for ladder in await self._ladders.all():
            for fmt in ladder.formats:
                name = keys.queue_name(ladder.ladder, fmt)
                grant = await self._grant(name)  # продлевается на каждом обходе
                if grant is None or time.monotonic() < self._next_at.get(name, 0.0):
                    continue
                self._next_at[name] = time.monotonic() + ladder.matchmaking.interval_s
                fence = QueueFence(
                    ladder=ladder.ladder,
                    format=fmt,
                    key=keys.queue_owner(name),
                    token=grant.token,
                )
                created += await self._matchmaker.run_queue(ladder, fmt, fence)
        return created

    async def _grant(self, name: str) -> Grant | None:
        grant = self.held.get(name)
        if grant is not None:
            if await self.lease.renew(grant):
                return grant
            del self.held[name]
            log.warning("match.matchmaker_lease_lost", queue=name)
        grant = await self.lease.acquire(name)
        if grant is not None:
            self.held[name] = grant
        return grant

    async def handoff(self) -> None:
        """Отдать очереди другим инстансам (SIGTERM): lease свободен сразу."""
        for grant in list(self.held.values()):
            with contextlib.suppress(RedisError, OSError):
                await self.lease.release(grant)
        self.held.clear()
