"""Процесс rooms: runner-ы, graceful stop с handoff, падения и таймауты (D6 этапа 4)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from prometheus_client import REGISTRY

from gv_service import (
    Infrastructure,
    Runner,
    RunnerFailedError,
    RunnersSettings,
    ServiceDefinition,
    ServiceSettings,
    supervise_runners,
)
from gv_service.runner import Command, Runtime

pytestmark = pytest.mark.unit

FAST = RunnersSettings(stop_timeout_s=0.2, handoff_timeout_s=0.2)


class FakeRunner:
    """Runner с управляемым поведением; журнал событий — общий на тест."""

    def __init__(
        self,
        name: str,
        journal: list[str],
        *,
        crash_after: float | None = None,
        exit_early: bool = False,
        ignore_stop: bool = False,
        handoff_hangs: bool = False,
        handoff_fails: bool = False,
    ) -> None:
        self._name = name
        self.journal = journal
        self.crash_after = crash_after
        self.exit_early = exit_early
        self.ignore_stop = ignore_stop
        self.handoff_hangs = handoff_hangs
        self.handoff_fails = handoff_fails
        self.started = asyncio.Event()
        self.stop_seen = False

    @property
    def name(self) -> str:
        return self._name

    async def run(self, stop: asyncio.Event) -> None:
        self.journal.append(f"{self._name}:run")
        self.started.set()
        if self.exit_early:
            return
        if self.crash_after is not None:
            await asyncio.sleep(self.crash_after)
            raise RuntimeError("сбой runner-а")
        if self.ignore_stop:
            await asyncio.Event().wait()  # зависает навсегда, пока не отменят
        await stop.wait()
        self.stop_seen = True
        self.journal.append(f"{self._name}:stopped")

    async def handoff(self) -> None:
        self.journal.append(f"{self._name}:handoff")
        if self.handoff_hangs:
            await asyncio.Event().wait()
        if self.handoff_fails:
            raise RuntimeError("сбой handoff")
        self.journal.append(f"{self._name}:handed_off")


async def _started(*runners: FakeRunner) -> None:
    async with asyncio.timeout(2):
        for r in runners:
            await r.started.wait()


def _metric(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_fake_runner_satisfies_protocol() -> None:
    assert isinstance(FakeRunner("x", []), Runner)


async def test_stop_then_handoff_in_order() -> None:
    journal: list[str] = []
    a, b = FakeRunner("t.a", journal), FakeRunner("t.b", journal)
    stop = asyncio.Event()
    task = asyncio.create_task(supervise_runners([a, b], stop, FAST))
    await _started(a, b)
    assert not task.done()

    stop.set()
    async with asyncio.timeout(2):
        await task
    assert a.stop_seen and b.stop_seen
    for name in ("t.a", "t.b"):
        # handoff — строго после выхода из run.
        assert journal.index(f"{name}:stopped") < journal.index(f"{name}:handoff")
        assert f"{name}:handed_off" in journal
    assert _metric("gv_runners_active", runner="t.a") == 0


async def test_crash_stops_others_with_handoff_and_raises() -> None:
    journal: list[str] = []
    bad = FakeRunner("t.crash", journal, crash_after=0.01)
    good = FakeRunner("t.survivor", journal)
    stop = asyncio.Event()
    before = _metric("gv_runner_events_total", runner="t.crash", event="crashed")

    with pytest.raises(RunnerFailedError) as error:
        async with asyncio.timeout(2):
            await supervise_runners([bad, good], stop, FAST)

    assert error.value.runners == ("t.crash",)
    # Соседний runner не брошен: остановлен и отдал работу.
    assert good.stop_seen
    assert "t.survivor:handed_off" in journal
    # handoff вызывается и у упавшего (best effort — освободить аренды).
    assert "t.crash:handed_off" in journal
    assert _metric("gv_runner_events_total", runner="t.crash", event="crashed") == before + 1


async def test_exit_without_stop_is_failure() -> None:
    journal: list[str] = []
    quitter = FakeRunner("t.quit", journal, exit_early=True)
    with pytest.raises(RunnerFailedError, match=r"t\.quit"):
        async with asyncio.timeout(2):
            await supervise_runners([quitter], asyncio.Event(), FAST)
    assert "t.quit:handed_off" in journal


async def test_handoff_timeout_does_not_block_others_or_fail() -> None:
    journal: list[str] = []
    slow = FakeRunner("t.slow", journal, handoff_hangs=True)
    broken = FakeRunner("t.broken", journal, handoff_fails=True)
    ok = FakeRunner("t.ok", journal)
    stop = asyncio.Event()
    task = asyncio.create_task(supervise_runners([slow, broken, ok], stop, FAST))
    await _started(slow, broken, ok)
    timeouts = _metric("gv_runner_events_total", runner="t.slow", event="handoff_timeout")

    stop.set()
    async with asyncio.timeout(2):
        await task  # без исключения: ошибки handoff процесс не роняют

    assert "t.ok:handed_off" in journal
    assert "t.slow:handed_off" not in journal
    assert _metric("gv_runner_events_total", runner="t.slow", event="handoff_timeout") == (
        timeouts + 1
    )
    assert _metric("gv_runner_events_total", runner="t.broken", event="handoff_error") >= 1


async def test_run_ignoring_stop_is_cancelled_after_timeout() -> None:
    journal: list[str] = []
    stuck = FakeRunner("t.stuck", journal, ignore_stop=True)
    stop = asyncio.Event()
    task = asyncio.create_task(supervise_runners([stuck], stop, FAST))
    await _started(stuck)
    stop.set()
    async with asyncio.timeout(2):
        await task
    assert "t.stuck:handed_off" in journal
    assert _metric("gv_runner_events_total", runner="t.stuck", event="stop_timeout") >= 1


async def test_cancelled_supervisor_still_hands_off() -> None:
    """Упал соседний компонент процесса — TaskGroup отменяет rooms; handoff всё равно идёт."""
    journal: list[str] = []
    r = FakeRunner("t.cancel", journal)
    task = asyncio.create_task(supervise_runners([r], asyncio.Event(), FAST))
    await _started(r)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        async with asyncio.timeout(2):
            await task
    assert r.stop_seen
    assert "t.cancel:handed_off" in journal


@pytest.mark.parametrize(
    ("names", "error"), [([], "нет runner-ов"), (["t.dup", "t.dup"], "повторяются")]
)
async def test_invalid_runner_set(names: list[str], error: str) -> None:
    runners = [FakeRunner(n, []) for n in names]
    with pytest.raises(ValueError, match=error):
        await supervise_runners(runners, asyncio.Event(), FAST)


# ----------------------------------------------------------------------------- Runtime
def _settings(**kw: Any) -> ServiceSettings:
    kw.setdefault("metrics_port", None)
    return ServiceSettings(service_name="demo", **kw)


def _definition(runners: list[Runner] | None) -> ServiceDefinition:
    return ServiceDefinition(
        name="demo",
        settings_cls=ServiceSettings,
        build_runners=None if runners is None else (lambda _infra: runners),
    )


@pytest.mark.parametrize("runners", [None, []])
def test_rooms_command_without_runners_fails_clearly(runners: list[Runner] | None) -> None:
    runtime = Runtime(_definition(runners), Command.ROOMS, _settings())
    infra = Infrastructure(settings=runtime.settings, tables=None)
    with pytest.raises(SystemExit, match="команда rooms: у сервиса demo нет"):
        runtime._components(infra, tracing=False)


def test_rooms_and_all_include_runners_and_metrics() -> None:
    runner = FakeRunner("t.rt", [])
    for command in (Command.ROOMS, Command.ALL):
        runtime = Runtime(_definition([runner]), command, _settings(metrics_port=9100))
        infra = Infrastructure(settings=runtime.settings, tables=None)
        components = runtime._components(infra, tracing=False)
        assert {"rooms", "metrics"} <= set(components)


def test_all_without_runners_skips_rooms() -> None:
    runtime = Runtime(_definition(None), Command.ALL, _settings())
    infra = Infrastructure(settings=runtime.settings, tables=None)
    assert "rooms" not in runtime._components(infra, tracing=False)


async def test_runtime_rooms_graceful_shutdown_hands_off_before_infra_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal: list[str] = []
    runner = FakeRunner("t.proc", journal)
    original_close = Infrastructure.close

    async def close(self: Infrastructure) -> None:
        journal.append("infra:close")
        await original_close(self)

    monkeypatch.setattr(Infrastructure, "close", close)
    runtime = Runtime(_definition([runner]), Command.ROOMS, _settings())
    task = asyncio.create_task(runtime.run())
    await _started(runner)

    runtime.request_shutdown()  # то же, что SIGTERM
    async with asyncio.timeout(5):
        await task
    assert journal[-3:] == ["t.proc:handoff", "t.proc:handed_off", "infra:close"]


async def test_runtime_rooms_crash_exits_with_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Упавший runner завершает процесс исключением → ненулевой код, оркестратор перезапустит."""
    runner = FakeRunner("t.proc_crash", [], crash_after=0.01)
    runtime = Runtime(_definition([runner]), Command.ROOMS, _settings())
    with pytest.raises(ExceptionGroup) as group:
        async with asyncio.timeout(5):
            await runtime.run()
    assert group.group_contains(RunnerFailedError)
    assert runtime.shutting_down


def test_runner_timeouts_must_fit_shutdown_budget() -> None:
    with pytest.raises(ValueError, match="GV_SHUTDOWN_TIMEOUT_S"):
        ServiceSettings(
            service_name="demo",
            shutdown_timeout_s=10,
            runners=RunnersSettings(stop_timeout_s=5, handoff_timeout_s=5),
        )
