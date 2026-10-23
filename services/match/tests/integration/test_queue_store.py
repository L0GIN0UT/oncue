"""Очередь подбора на реальном Redis (ADR 0038): lease с fencing, резерв пары, срыв отсчёта.

Пара не создаётся дважды и после потери lease; запись без heartbeat исчезает; срыв отсчёта
возвращает игрока в поиск с прежним ``entered_at``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from match_service.application.ports import QueueFence
from match_service.infrastructure.queue import RedisQueue
from match_service.infrastructure.rooms import keys
from match_service.infrastructure.rooms.lease import Lease
from redis.asyncio import Redis

from gv_core.ids import uuid7
from gv_testkit import InfraProvider

pytestmark = pytest.mark.integration

NAME = keys.queue_name("music", "classic")


@pytest.fixture
async def redis(infra: InfraProvider) -> AsyncIterator[Redis]:
    client = Redis.from_url(infra.redis_url(), socket_timeout=2)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


def _lease(redis: Redis, instance: str, ttl_ms: int = 5000) -> Lease:
    return Lease(
        redis,
        instance,
        ttl_ms,
        owner_key=keys.queue_owner,
        epoch_key=keys.queue_epoch,
        snapshot_key=keys.queue_unused,
    )


def _fence(token: str) -> QueueFence:
    return QueueFence(ladder="music", format="classic", key=keys.queue_owner(NAME), token=token)


async def _players(queue: RedisQueue, *ratings: int) -> list[UUID]:
    users = [uuid7() for _ in ratings]
    for user, rating in zip(users, ratings, strict=True):
        await queue.enter(user, "music", "classic", rating, 1_000)
    return users


async def test_enter_is_idempotent_and_waiting_lists_everyone(redis: Redis) -> None:
    queue = RedisQueue(redis, ttl_ms=5000, matched_ttl_ms=5000)
    a, b = await _players(queue, 1200, 900)
    again, created = await queue.enter(a, "music", "classic", 1500, 9_999)
    assert created is False and again.rating == 1200 and again.entered_at_ms == 1_000
    assert {r.user_id for r in await queue.waiting("music", "classic", 10)} == {a, b}
    assert await queue.count("music", "classic") == 2
    await queue.remove(a)
    await queue.remove(a)
    assert await queue.count("music", "classic") == 1
    assert await queue.get(a) is None


async def test_record_without_heartbeat_disappears_and_zset_is_pruned(redis: Redis) -> None:
    queue = RedisQueue(redis, ttl_ms=150, matched_ttl_ms=5000)
    (a,) = await _players(queue, 1000)
    assert await queue.touch(a) is True
    await asyncio.sleep(0.3)
    assert await queue.get(a) is None and await queue.touch(a) is False
    assert await queue.waiting("music", "classic", 10) == []
    assert await queue.count("music", "classic") == 0  # осиротевший член вычищен


async def test_only_the_lease_holder_can_reserve_a_pair_once(redis: Redis) -> None:
    queue = RedisQueue(redis, ttl_ms=5000, matched_ttl_ms=5000)
    first, second = _lease(redis, "one"), _lease(redis, "two")
    grant = await first.acquire(NAME)
    assert grant is not None and await second.acquire(NAME) is None
    a, b = await _players(queue, 1000, 1010)
    stale = _fence("two|1")
    assert await queue.reserve(stale, a, b, "ROOM2222", 1_000) is False  # чужой токен
    fence = _fence(grant.token)
    assert await queue.reserve(fence, a, b, "ROOM2222", 1_000) is True
    assert await queue.reserve(fence, a, b, "ROOM3333", 1_000) is False  # пара уже занята
    assert await queue.count("music", "classic") == 0
    # Во время создания комнаты игрок по-прежнему «ищет»; после confirm — matched.
    reserved = await queue.get(a)
    assert reserved is not None and reserved.state == "reserved"
    assert await queue.waiting("music", "classic", 10) == []
    assert await queue.confirm("music", "classic", a, b, "ROOM2222") is True
    matched = await queue.get(a)
    assert matched is not None and matched.state == "matched"
    assert matched.room_code == "ROOM2222"


async def test_lease_epoch_grows_after_loss_and_old_holder_is_fenced(redis: Redis) -> None:
    queue = RedisQueue(redis, ttl_ms=5000, matched_ttl_ms=5000)
    old, new = _lease(redis, "old", 200), _lease(redis, "new", 200)
    stale = await old.acquire(NAME)
    assert stale is not None
    await asyncio.sleep(0.35)  # lease истёк (пауза «зомби»)
    fresh = await new.acquire(NAME)
    assert fresh is not None and fresh.epoch == stale.epoch + 1
    assert await old.renew(stale) is False
    a, b = await _players(queue, 1000, 1000)
    assert await queue.reserve(_fence(stale.token), a, b, "ROOM2222", 1) is False
    assert await queue.reserve(_fence(fresh.token), a, b, "ROOM2222", 1) is True


async def test_requeue_keeps_entered_at_and_only_for_the_same_room(redis: Redis) -> None:
    queue = RedisQueue(redis, ttl_ms=5000, matched_ttl_ms=5000)
    grant = await _lease(redis, "one").acquire(NAME)
    assert grant is not None
    a, b = await _players(queue, 1000, 1000)
    await queue.reserve(_fence(grant.token), a, b, "ROOM2222", 1)
    await queue.confirm("music", "classic", a, b, "ROOM2222")
    assert await queue.requeue(a, "OTHER222") is False
    assert await queue.requeue(a, "ROOM2222") is True
    assert await queue.requeue(a, "ROOM2222") is False  # уже в поиске
    back = await queue.get(a)
    assert back is not None and back.state == "searching" and back.entered_at_ms == 1_000
    assert [r.user_id for r in await queue.waiting("music", "classic", 10)] == [a]
    # Ушедший соперник: запись убирается, а не возвращается в поиск.
    await queue.release(b, "ROOM2222")
    assert await queue.get(b) is None


async def test_waiting_takes_the_oldest_first_so_the_top_is_never_starved(redis: Redis) -> None:
    queue = RedisQueue(redis, ttl_ms=5000, matched_ttl_ms=5000)
    users = []
    for index, rating in enumerate((3000, 100, 2900, 200, 3100)):
        user = uuid7()
        users.append(user)
        await queue.enter(user, "music", "classic", rating, 1_000 + index)
    oldest = await queue.waiting("music", "classic", 2)
    # Верх рейтинга (3000) вошёл первым и обслуживается, хотя батч меньше очереди.
    assert [r.user_id for r in oldest] == users[:2]


async def test_stale_reserved_lists_only_old_reserves_and_requeue_returns_them(
    redis: Redis,
) -> None:
    queue = RedisQueue(redis, ttl_ms=5000, matched_ttl_ms=5000, reserved_ttl_ms=4000)
    grant = await _lease(redis, "one").acquire(NAME)
    assert grant is not None
    a, b = await _players(queue, 1000, 1000)
    await queue.reserve(_fence(grant.token), a, b, "ROOM2222", now_ms=10_000)
    assert await queue.stale_reserved("music", "classic", 9_000) == []  # ещё свежий
    stale = await queue.stale_reserved("music", "classic", 11_000)
    assert {r.user_id for r in stale} == {a, b} and stale[0].room_code == "ROOM2222"
    for record in stale:
        assert await queue.requeue(record.user_id, "ROOM2222") is True
    assert await queue.stale_reserved("music", "classic", 99_999) == []
    assert {r.user_id for r in await queue.waiting("music", "classic", 10)} == {a, b}


async def test_tick_extends_reserved_record_ttl(redis: Redis) -> None:
    queue = RedisQueue(redis, ttl_ms=5000, matched_ttl_ms=5000, reserved_ttl_ms=500)
    grant = await _lease(redis, "one").acquire(NAME)
    assert grant is not None
    a, b = await _players(queue, 1000, 1000)
    await queue.reserve(_fence(grant.token), a, b, "ROOM2222", 1)
    await asyncio.sleep(0.3)
    assert await queue.touch(a) is True  # продлевает резерв
    await asyncio.sleep(0.3)
    assert await queue.get(a) is not None and await queue.get(b) is None
