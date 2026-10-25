"""Фоновая очередь отзыва подписок и соединений Centrifugo (хвост 4a, ADR 0052/0059/0063).

Повторы ``unsubscribe``/``disconnect`` с паузой (до ``GV_MATCH__UNSUBSCRIBE_ATTEMPTS``)
раньше шли прямо в цикле владельца комнаты и тормозили её при недоступном Centrifugo.
Теперь вызов только кладёт задание в очередь процесса и возвращается сразу, а
``GV_MATCH__REVOCATION_WORKERS`` воркеров выполняют отзыв с повторами внутри адаптера.

* очередь ограничена ``GV_MATCH__REVOCATION_QUEUE_SIZE``; переполнение не блокирует
  команду игрока: лог ``match.revocation_dropped`` и ``gv_match_revocation_dropped_total``;
* воркеры стартуют лениво (первое задание) - очередь работает в любом процессе (api,
  rooms, consumer) без отдельной регистрации;
* остановка (SIGTERM, ``aclose``): ждём очередь до ``GV_MATCH__REVOCATION_DRAIN_S``, остаток
  сбрасывается (метрика и лог). Подписка и так умрёт вместе с токеном (TTL), а новый токен
  санкционированному не выдаётся.

Бан аккаунта (``account_banned``) очередь НЕ использует: ``DirectRevoker`` вызывает адаптер
синхронно из consumer-а (с повторами адаптера), чтобы задание нельзя было потерять
(переполнение, ``aclose``, сброс при SIGTERM), а повтор события невозможен (ADR 0059, 0063).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import structlog
from prometheus_client import Counter, Gauge

from match_service.application.ports import Subscriptions
from match_service.infrastructure.realtime import DISCONNECT_FAILED, UNSUBSCRIBE_FAILED

log = structlog.get_logger(__name__)

REVOCATION_DROPPED = Counter(
    "gv_match_revocation_dropped_total",
    "Отзывы Centrifugo, не выполненные: очередь переполнена или сброшена при остановке",
    ["kind"],
)
REVOCATION_DEPTH = Gauge("gv_match_revocation_queue_depth", "Заданий отзыва в очереди процесса")


@dataclass(frozen=True, slots=True)
class _Job:
    kind: str
    run: Callable[[], Awaitable[None]]


class DirectRevoker:
    """Синхронный отзыв соединений для бана аккаунта: мимо очереди, без потери задания.

    Повторы - внутри адаптера. Провал после них - лог и ``gv_match_disconnect_failed_total``;
    исключение не бросается, иначе откатится проекция санкций (ADR 0059).
    """

    def __init__(self, inner: Subscriptions) -> None:
        self._inner = inner

    async def disconnect(self, user_id: UUID, *, reconnect: bool, reason: str) -> None:
        """Разорвать соединения пользователя прямо сейчас."""
        try:
            await self._inner.disconnect(user_id, reconnect=reconnect, reason=reason)
        except Exception:
            DISCONNECT_FAILED.inc()
            log.exception("match.direct_disconnect_failed", reason=reason)


class RevocationQueue:
    """``Subscriptions`` с очередью: вызов не ждёт Centrifugo."""

    def __init__(
        self, inner: Subscriptions, *, workers: int, capacity: int, drain_s: float
    ) -> None:
        if workers < 1 or capacity < 1:
            raise ValueError("workers и capacity должны быть >= 1")
        self._inner = inner
        self._workers_count = workers
        self._drain_s = drain_s
        self._queue: asyncio.Queue[_Job] = asyncio.Queue(maxsize=capacity)
        self._workers: list[asyncio.Task[None]] = []
        self._closed = False

    async def unsubscribe(self, user_id: UUID, channel: str) -> None:
        """Поставить отзыв подписки в очередь (не ждёт Centrifugo)."""
        self._put(_Job("unsubscribe", lambda: self._inner.unsubscribe(user_id, channel)))

    async def disconnect(self, user_id: UUID, *, reconnect: bool, reason: str) -> None:
        """Поставить разрыв соединений в очередь (не ждёт Centrifugo)."""
        self._put(
            _Job(
                "disconnect",
                lambda: self._inner.disconnect(user_id, reconnect=reconnect, reason=reason),
            )
        )

    def _put(self, job: _Job) -> None:
        if self._closed:
            self._drop(job, "closed")
            return
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            self._drop(job, "overflow")
            return
        REVOCATION_DEPTH.set(self._queue.qsize())
        if not self._workers:
            self._workers = [
                asyncio.create_task(self._work(), name=f"match.revocation:{i}")
                for i in range(self._workers_count)
            ]

    @staticmethod
    def _drop(job: _Job, reason: str) -> None:
        REVOCATION_DROPPED.labels(job.kind).inc()
        log.error("match.revocation_dropped", kind=job.kind, reason=reason)

    async def _work(self) -> None:
        while True:
            job = await self._queue.get()
            REVOCATION_DEPTH.set(self._queue.qsize())
            try:
                await job.run()
            except Exception:
                # Адаптер сам гасит сбои после повторов; сюда - только неожиданное.
                (UNSUBSCRIBE_FAILED if job.kind == "unsubscribe" else DISCONNECT_FAILED).inc()
                log.exception("match.revocation_failed", kind=job.kind)
            finally:
                self._queue.task_done()

    async def aclose(self) -> None:
        """Остановка: дождаться очереди до ``drain_s``, остаток сбросить."""
        self._closed = True
        if self._workers:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._queue.join(), self._drain_s)
        while True:
            try:
                job = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._drop(job, "shutdown")
            self._queue.task_done()
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        REVOCATION_DEPTH.set(0)
