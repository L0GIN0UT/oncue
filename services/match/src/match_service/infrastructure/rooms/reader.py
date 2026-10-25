"""Один потребитель потоков команд на инстанс ``rooms`` (ADR 0063).

Раньше каждый ``RoomActor`` держал своё блокирующее ``XREAD`` — ~300 соединений Redis на
инстанс. Теперь блокирующее чтение одно: ``XREAD`` сразу по ключам всех комнат инстанса,
записи раздаются в «почтовые ящики» комнат, а актёр ждёт свой ящик в памяти.

Гарантии:

* порядок — записи комнаты приходят из одного потока в порядке id, ящик — очередь FIFO;
* без потерь — ``XREAD`` ничего не удаляет, а курсор чтения двигается только после
  разбора ответа; смена набора ключей отменяет вызов и собирает новый (ответ, не дошедший
  до разбора, будет прочитан заново); подтверждение команд по-прежнему курсор снимка;
* рассинхрон курсора — актёр вернулся к снимку (откат после сбоя) и передал курсор,
  которого ящик не ждёт: ящик очищается и читается заново с переданного курсора;
* число соединений к Redis не зависит от числа комнат — одно блокирующее чтение.

Redis Cluster: ключи ``{room:CODE}`` лежат в разных слотах, многоключевой ``XREAD`` там
невозможен (``CROSSSLOT``); сейчас Redis одиночный. Для кластера читатель нужно делить по
слотам (ADR 0063).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from prometheus_client import Counter, Gauge
from redis.asyncio import Redis
from redis.exceptions import RedisError

from match_service.application.ports import RoomCommand
from match_service.infrastructure.rooms import keys

log = structlog.get_logger(__name__)

READER_STREAMS = Gauge(
    "gv_match_command_reader_streams", "Потоки комнат в общем блокирующем XREAD инстанса"
)
READER_RECOMPOSE = Counter(
    "gv_match_command_reader_recompose_total",
    "Пересборки набора потоков общего XREAD (комната появилась/ушла/откат курсора)",
)
READER_ERRORS = Counter(
    "gv_match_command_reader_errors_total", "Сбои общего XREAD (повтор после паузы)"
)

type Entry = tuple[str, RoomCommand | None]
"""Запись потока: id и команда (``None`` — запись не разобрана)."""
type Parse = Callable[[Any], RoomCommand | None]  # Any: поле записи Redis (bytes | str | None)


@dataclass(slots=True)
class _Box:
    """Ящик комнаты: уже прочитанные, но ещё не выданные актёру записи."""

    consumed: str  # курсор выданного актёру; ожидаемый курсор следующего ``read``
    fetched: str  # курсор прочитанного из Redis
    entries: deque[Entry] = field(default_factory=deque)
    ready: asyncio.Event = field(default_factory=asyncio.Event)


def _text(value: Any) -> str:  # Any: ответ redis-py
    return value.decode() if isinstance(value, bytes) else str(value)


class SharedCommandReader:
    """Общий читатель потоков команд комнат инстанса."""

    def __init__(
        self,
        redis: Redis,
        *,
        poll_ms: int,
        batch: int,
        parse: Parse,
        field_name: str,
        backoff_max_s: float = 5.0,
    ) -> None:
        self._redis = redis
        self._poll_ms = max(1, poll_ms)
        self._batch = batch
        self._parse = parse
        self._field = field_name
        self._backoff_max_s = max(self._poll_ms / 1000, backoff_max_s)
        self._boxes: dict[str, _Box] = {}
        self._changed = asyncio.Event()

    @property
    def watched(self) -> int:
        """Сколько комнат читается."""
        return len(self._boxes)

    # ------------------------------------------------------------------ актёры
    async def read(self, code: str, cursor: str, *, block_ms: int, count: int) -> list[Entry]:
        """Записи комнаты после ``cursor``; ждать не дольше ``block_ms``."""
        box = self._boxes.get(code)
        if box is None or box.consumed != cursor:
            box = _Box(consumed=cursor, fetched=cursor)
            self._boxes[code] = box
            self._touch()
        if not box.entries:
            box.ready.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(box.ready.wait(), max(1, block_ms) / 1000)
        out: list[Entry] = []
        while box.entries and len(out) < count:
            out.append(box.entries.popleft())
        if out:
            box.consumed = out[-1][0]
        return out

    def detach(self, code: str) -> None:
        """Комната ушла от инстанса: больше не читать."""
        if self._boxes.pop(code, None) is not None:
            self._touch()

    def _touch(self) -> None:
        READER_RECOMPOSE.inc()
        READER_STREAMS.set(len(self._boxes))
        self._changed.set()

    # ------------------------------------------------------------------ читатель
    async def run(self) -> None:
        """Читать потоки до отмены (отмена безопасна: ``XREAD`` ничего не удаляет).

        Любой сбой цикла (не только Redis) не останавливает чтение: пауза с растущим
        потолком ``backoff_max_s`` и повтор с теми же курсорами в памяти - ящики и
        ``fetched`` сохраняются, ничего не теряется (ADR 0063).
        """
        failures = 0
        while True:
            plan = {code: box.fetched for code, box in self._boxes.items()}
            if not plan:
                self._changed.clear()
                await self._changed.wait()
                continue
            self._changed.clear()
            try:
                await self._cycle(plan)
                failures = 0
            except (RedisError, OSError, TimeoutError) as exc:
                READER_ERRORS.inc()
                log.warning("match.command_reader_failed", error_type=type(exc).__name__)
                failures += 1
                await asyncio.sleep(self._backoff(failures))
            except Exception:
                READER_ERRORS.inc()
                log.exception("match.command_reader_crashed")
                failures += 1
                await asyncio.sleep(self._backoff(failures))

    def _backoff(self, failures: int) -> float:
        base = self._poll_ms / 1000
        return float(min(self._backoff_max_s, base * 2 ** (failures - 1)))

    async def _cycle(self, plan: dict[str, str]) -> None:
        """Один блокирующий ``XREAD`` по набору ``plan``; прерывается сменой набора."""
        streams = {keys.commands(code): cursor for code, cursor in plan.items()}
        read = asyncio.create_task(self._xread(streams))
        changed = asyncio.create_task(self._changed.wait())
        try:
            await asyncio.wait({read, changed}, return_when=asyncio.FIRST_COMPLETED)
            if read.done():
                self._deliver(plan, read.result())
        finally:
            for task in (read, changed):
                if not task.done():
                    task.cancel()
            await asyncio.gather(read, changed, return_exceptions=True)

    async def _xread(self, streams: dict[Any, Any]) -> Any:  # Any: типы redis-py XREAD
        return await self._redis.xread(streams, count=self._batch, block=self._poll_ms)

    def _deliver(self, plan: dict[str, str], result: Any) -> None:  # Any: ответ XREAD
        by_key = {keys.commands(code): code for code in plan}
        for raw_key, entries in result or []:
            code = by_key.get(_text(raw_key))
            box = self._boxes.get(code) if code is not None else None
            if code is None or box is None or box.fetched != plan[code]:
                continue  # комната ушла или откатила курсор, пока шло чтение
            for entry_id, fields in entries:
                raw = fields.get(self._field.encode()) or fields.get(self._field)
                box.entries.append((_text(entry_id), self._parse(raw)))
                box.fetched = _text(entry_id)
            box.ready.set()
