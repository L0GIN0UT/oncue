"""Долгоживущие фоновые компоненты процесса ``rooms`` (D6 плана этапа 4, ADR 0034).

``Runner`` — компонент, который держит у себя работу дольше одного сообщения: например,
владелец комнат match с таймерами и арендой (lease). Такие компоненты живут в отдельном
процессе ``rooms`` (в ``all`` — вместе с остальными), а не в ``api``: деплой API не должен
рвать идущие матчи.

Жизненный цикл одного runner-а:

1. ``run(stop)`` работает, пока не выставлен ``stop``. После сигнала runner перестаёт брать
   новую работу, доделывает текущую операцию и возвращается.
2. ``handoff()`` вызывается после того, как ``run`` вернулся (или был отменён по таймауту):
   runner отдаёт удерживаемую работу другим инстансам (снимок, снятие lease).

Остановка процесса (SIGTERM) — общий ``stop`` → ожидание ``run`` не дольше
``GV_RUNNERS__STOP_TIMEOUT_S`` → ``handoff`` всех runner-ов параллельно, каждый не дольше
``GV_RUNNERS__HANDOFF_TIMEOUT_S`` → закрытие инфраструктуры (в ``Runtime``).

Падение одного runner-а (исключение или самовольный выход из ``run`` без ``stop``)
останавливает и остальные runner-ы процесса: они проходят тот же graceful handoff, после
чего ``supervise_runners`` поднимает ``RunnerFailedError`` и процесс завершается с ненулевым
кодом — оркестратор его перезапустит. Полуживой процесс, где часть комнат без владельца, хуже
честного перезапуска. Ошибка или таймаут ``handoff`` логируются и считаются в метриках, но
процесс не роняют: он и так останавливается, а недоданную работу заберут по истечении аренды.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import structlog
from prometheus_client import Counter, Gauge, Histogram

from gv_service.settings import RunnersSettings

log = structlog.get_logger(__name__)

RUNNERS_ACTIVE = Gauge("gv_runners_active", "Работающие runner-ы процесса rooms", ["runner"])
RUNNER_EVENTS = Counter(
    "gv_runner_events_total",
    "События жизненного цикла runner-ов: crashed, exited_early, stop_timeout, "
    "handoff_ok, handoff_error, handoff_timeout",
    ["runner", "event"],
)
RUNNER_HANDOFF_SECONDS = Histogram(
    "gv_runner_handoff_seconds", "Длительность handoff runner-а при остановке", ["runner"]
)


@runtime_checkable
class Runner(Protocol):
    """Долгоживущий фоновый компонент процесса ``rooms``.

    Attributes:
        name: Имя для логов и меток метрик (``match.rooms``); уникально в процессе.
    """

    @property
    def name(self) -> str:
        """Имя runner-а."""
        ...

    async def run(self, stop: asyncio.Event) -> None:
        """Работать до сигнала ``stop``.

        После ``stop`` — не брать новую работу, завершить текущую операцию и вернуться.
        Возврат без ``stop`` считается сбоем процесса.
        """
        ...

    async def handoff(self) -> None:
        """Передать удерживаемую работу другим инстансам; вызывается после ``run``.

        Вызывается и после падения ``run`` (best effort), поэтому должен быть безопасен в
        любом состоянии runner-а. Ограничен ``GV_RUNNERS__HANDOFF_TIMEOUT_S``.
        """
        ...


class RunnerFailedError(RuntimeError):
    """Один или несколько runner-ов упали или вышли без сигнала остановки."""

    def __init__(self, runners: Sequence[str]) -> None:
        self.runners = tuple(runners)
        super().__init__(f"runner-ы завершились аварийно: {', '.join(self.runners)}")


async def supervise_runners(
    runners: Sequence[Runner], stop: asyncio.Event, settings: RunnersSettings
) -> None:
    """Запустить runner-ы и провести их через graceful stop.

    Возвращается после ``stop`` и handoff всех runner-ов. Если какой-то runner упал,
    остальные тоже останавливаются (с handoff), затем поднимается ``RunnerFailedError``.
    Отмена задачи супервизора (упал соседний компонент процесса) тоже проходит handoff.

    Raises:
        ValueError: Пустой список или повтор имени.
        RunnerFailedError: Падение или самовольный выход runner-а.
    """
    names = [r.name for r in runners]
    if not names:
        raise ValueError("нет runner-ов для запуска")
    if len(set(names)) != len(names):
        raise ValueError(f"имена runner-ов повторяются: {names}")

    # Свой сигнал: выставляется и внешним stop, и падением любого runner-а.
    local_stop = asyncio.Event()
    failed: list[str] = []

    def fail(runner: Runner, event: str) -> None:
        RUNNER_EVENTS.labels(runner.name, event).inc()
        failed.append(runner.name)
        local_stop.set()

    async def drive(runner: Runner) -> None:
        RUNNERS_ACTIVE.labels(runner.name).set(1)
        try:
            await runner.run(local_stop)
        except Exception:
            log.exception("runner.crashed", runner=runner.name)
            fail(runner, "crashed")
            return
        finally:
            RUNNERS_ACTIVE.labels(runner.name).set(0)
        if not local_stop.is_set():
            log.error("runner.exited_early", runner=runner.name)
            fail(runner, "exited_early")

    tasks = [asyncio.create_task(drive(r), name=f"runner:{r.name}") for r in runners]
    log.info("runners.started", runners=names)
    waiters = [asyncio.create_task(stop.wait()), asyncio.create_task(local_stop.wait())]
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # И штатная остановка, и отмена супервизора: сначала перестать брать работу и
        # дождаться run, затем handoff. Ожидания ограничены таймаутами настроек.
        local_stop.set()
        for waiter in waiters:
            waiter.cancel()
        await _drain(runners, tasks, settings.stop_timeout_s)
        await asyncio.gather(*(_handoff(r, settings.handoff_timeout_s) for r in runners))
        log.info("runners.stopped", failed=failed)
    if failed:
        raise RunnerFailedError(failed)


async def _drain(
    runners: Sequence[Runner], tasks: list[asyncio.Task[None]], limit_s: float
) -> None:
    """Дождаться выхода из ``run``; зависшие отменить."""
    _, pending = await asyncio.wait(tasks, timeout=limit_s)
    for runner, task in zip(runners, tasks, strict=True):
        if task in pending:
            log.error("runner.stop_timeout", runner=runner.name, timeout_s=limit_s)
            RUNNER_EVENTS.labels(runner.name, "stop_timeout").inc()
            task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def _handoff(runner: Runner, limit_s: float) -> None:
    """Один handoff: ошибка и таймаут логируются, но не прерывают handoff остальных."""
    started = time.perf_counter()
    try:
        async with asyncio.timeout(limit_s):
            await runner.handoff()
    except TimeoutError:
        # Таймаут — не сбой кода: трассировка отмены ничего не добавит.
        log.error("runner.handoff_timeout", runner=runner.name, timeout_s=limit_s)  # noqa: TRY400
        RUNNER_EVENTS.labels(runner.name, "handoff_timeout").inc()
    except Exception:
        log.exception("runner.handoff_failed", runner=runner.name)
        RUNNER_EVENTS.labels(runner.name, "handoff_error").inc()
    else:
        log.info("runner.handed_off", runner=runner.name)
        RUNNER_EVENTS.labels(runner.name, "handoff_ok").inc()
    finally:
        RUNNER_HANDOFF_SECONDS.labels(runner.name).observe(time.perf_counter() - started)
