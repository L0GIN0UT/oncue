"""Фоновая очередь отзыва подписок и соединений Centrifugo (пункт 3 хвоста 4a)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from uuid import UUID

import pytest
from match_service.infrastructure.realtime import DISCONNECT_FAILED, UNSUBSCRIBE_FAILED
from match_service.infrastructure.revocation import (
    REVOCATION_DROPPED,
    DirectRevoker,
    RevocationQueue,
)

from gv_core.ids import uuid7

pytestmark = pytest.mark.unit


class Inner:
    """Centrifugo-двойник: можно «зависнуть» до отпускания шлюза."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.gate.set()
        self.unsubscribed: list[tuple[UUID, str]] = []
        self.disconnected: list[tuple[UUID, bool, str]] = []
        self.fail = False
        self.running = 0
        self.peak = 0

    async def unsubscribe(self, user_id: UUID, channel: str) -> None:
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await self.gate.wait()
            if self.fail:
                raise ConnectionError("centrifugo недоступен")
            self.unsubscribed.append((user_id, channel))
        finally:
            self.running -= 1

    async def disconnect(self, user_id: UUID, *, reconnect: bool, reason: str) -> None:
        await self.gate.wait()
        if self.fail:
            raise ConnectionError("centrifugo недоступен")
        self.disconnected.append((user_id, reconnect, reason))


async def _eventually(check: Callable[[], object]) -> None:
    async with asyncio.timeout(3):
        while not check():  # noqa: ASYNC110 - опрос условия с таймаутом
            await asyncio.sleep(0.005)


def _dropped(kind: str) -> float:
    return REVOCATION_DROPPED.labels(kind)._value.get()


async def test_enqueue_does_not_wait_for_slow_centrifugo() -> None:
    inner = Inner()
    inner.gate.clear()  # Centrifugo «висит»
    queue = RevocationQueue(inner, workers=2, capacity=10, drain_s=0.05)
    user = uuid7()
    async with asyncio.timeout(1):  # вызов владельца возвращается сразу
        await queue.unsubscribe(user, "room:A")
        await queue.disconnect(user, reconnect=True, reason="kicked")
    inner.gate.set()
    await _eventually(lambda: inner.unsubscribed and inner.disconnected)
    assert inner.unsubscribed == [(user, "room:A")]
    assert inner.disconnected == [(user, True, "kicked")]
    await queue.aclose()


async def test_concurrency_is_bounded_by_workers() -> None:
    inner = Inner()
    inner.gate.clear()
    queue = RevocationQueue(inner, workers=3, capacity=50, drain_s=0.05)
    for i in range(20):
        await queue.unsubscribe(uuid7(), f"room:{i}")
    await _eventually(lambda: inner.running == 3)
    inner.gate.set()
    await _eventually(lambda: len(inner.unsubscribed) == 20)
    assert inner.peak == 3
    await queue.aclose()


async def test_overflow_is_counted_logged_and_does_not_block() -> None:
    inner = Inner()
    inner.gate.clear()
    queue = RevocationQueue(inner, workers=1, capacity=2, drain_s=0.05)
    before = _dropped("unsubscribe")
    await queue.unsubscribe(uuid7(), "room:0")  # заберёт единственный воркер
    await _eventually(lambda: inner.running == 1)
    async with asyncio.timeout(1):
        for i in range(5):
            await queue.unsubscribe(uuid7(), f"room:{i + 1}")
    assert _dropped("unsubscribe") == before + 3  # ёмкость 2: два в очереди, три отброшены
    inner.gate.set()
    await queue.aclose()


async def test_unexpected_failure_is_counted_and_worker_survives() -> None:
    inner = Inner()
    inner.fail = True
    queue = RevocationQueue(inner, workers=1, capacity=10, drain_s=0.05)
    u_before = UNSUBSCRIBE_FAILED._value.get()
    d_before = DISCONNECT_FAILED._value.get()
    await queue.unsubscribe(uuid7(), "room:A")
    await queue.disconnect(uuid7(), reconnect=False, reason="account_banned")
    await _eventually(lambda: UNSUBSCRIBE_FAILED._value.get() == u_before + 1)
    await _eventually(lambda: DISCONNECT_FAILED._value.get() == d_before + 1)
    inner.fail = False
    user = uuid7()
    await queue.unsubscribe(user, "room:B")
    await _eventually(lambda: inner.unsubscribed == [(user, "room:B")])
    await queue.aclose()


async def test_close_drains_queue_within_timeout() -> None:
    inner = Inner()
    queue = RevocationQueue(inner, workers=2, capacity=10, drain_s=2)
    for i in range(6):
        await queue.unsubscribe(uuid7(), f"room:{i}")
    await queue.aclose()  # SIGTERM: дождаться очереди
    assert len(inner.unsubscribed) == 6


async def test_close_drops_the_rest_after_timeout() -> None:
    inner = Inner()
    inner.gate.clear()  # Centrifugo не отвечает
    queue = RevocationQueue(inner, workers=1, capacity=10, drain_s=0.05)
    before = _dropped("unsubscribe")
    for i in range(4):
        await queue.unsubscribe(uuid7(), f"room:{i}")
    await _eventually(lambda: inner.running == 1)
    async with asyncio.timeout(2):
        await queue.aclose()
    assert _dropped("unsubscribe") == before + 3  # очередь сброшена
    await queue.unsubscribe(uuid7(), "room:late")  # после закрытия - не блокирует, считается
    assert _dropped("unsubscribe") == before + 4


async def test_ban_disconnect_is_synchronous_when_queue_is_full() -> None:
    inner = Inner()
    inner.gate.clear()
    queue = RevocationQueue(inner, workers=1, capacity=1, drain_s=0.05)
    await queue.unsubscribe(uuid7(), "room:0")  # займёт единственный воркер
    await _eventually(lambda: inner.running == 1)
    await queue.unsubscribe(uuid7(), "room:1")  # очередь полна
    inner.gate.set()
    user = uuid7()
    async with asyncio.timeout(1):
        await DirectRevoker(inner).disconnect(user, reconnect=False, reason="account_banned")
    assert inner.disconnected == [(user, False, "account_banned")]
    await queue.aclose()


async def test_ban_disconnect_works_after_queue_closed() -> None:
    inner = Inner()
    queue = RevocationQueue(inner, workers=1, capacity=2, drain_s=0.05)
    await queue.aclose()
    user = uuid7()
    await DirectRevoker(inner).disconnect(user, reconnect=False, reason="account_banned")
    assert inner.disconnected == [(user, False, "account_banned")]


async def test_ban_disconnect_failure_is_counted_and_not_raised() -> None:
    inner = Inner()
    inner.fail = True
    before = DISCONNECT_FAILED._value.get()
    await DirectRevoker(inner).disconnect(uuid7(), reconnect=False, reason="account_banned")
    assert DISCONNECT_FAILED._value.get() == before + 1
