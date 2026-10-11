"""Периодические задачи: взаимное исключение реплик, ошибки, встроенная очистка, closers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import func, insert, select

from gv_core.ids import uuid7
from gv_db import create_metadata
from gv_db.idempotency import idempotency_table
from gv_messaging.envelope import EventEnvelope
from gv_messaging.outbox import OutboxWriter
from gv_observability.metrics import render_metrics
from gv_service import (
    Infrastructure,
    JobResult,
    PeriodicJob,
    PeriodicJobRunner,
    ServiceDefinition,
    ServiceSettings,
)
from gv_service.builtin_jobs import builtin_periodic_jobs, collect_periodic_jobs
from gv_service.periodic import job_lock_key
from gv_service.runner import Command, Runtime
from gv_testkit import FakeClock, FakeRandom, create_schema, run_periodic_job


def _settings(database_url: str | None = None, **kw: Any) -> ServiceSettings:
    kw.setdefault("metrics_port", None)
    if database_url is not None:
        kw["database_url"] = SecretStr(database_url)
    return ServiceSettings(service_name="demo", **kw)


async def _noop() -> None:
    return None


# ----------------------------------------------------------------------------- unit
@pytest.mark.unit
class TestDeclaration:
    @pytest.mark.parametrize(
        ("name", "interval", "jitter"),
        [("Bad Name", 1, 0), ("ok.job", 0, 0), ("ok.job", 1, -1), (".x", 1, 0)],
    )
    def test_invalid_job_is_rejected(self, name: str, interval: float, jitter: float) -> None:
        with pytest.raises(ValueError, match=r"задачи|interval"):
            PeriodicJob(name, interval, _noop, jitter)

    def test_duplicate_names_are_rejected(self) -> None:
        jobs = [PeriodicJob("a.b", 1, _noop), PeriodicJob("a.b", 2, _noop)]
        with pytest.raises(ValueError, match="повторяются"):
            PeriodicJobRunner("demo", jobs, sessions=None)  # type: ignore[arg-type]

    def test_lock_key_is_stable_and_per_service(self) -> None:
        assert job_lock_key("game", "x") == job_lock_key("game", "x")
        assert job_lock_key("game", "x") != job_lock_key("catalog", "x")
        assert 0 <= job_lock_key("game", "x") < 2**32  # влезает в bigint advisory lock

    def test_no_builtin_jobs_without_database(self) -> None:
        infra = Infrastructure(settings=_settings(), tables=None)
        assert builtin_periodic_jobs(infra) == []

    def test_builtin_idempotency_job_only_with_table(self) -> None:
        plain, with_keys = create_metadata(), create_metadata()
        idempotency_table(with_keys.metadata)
        sessions: Any = object()  # задачи только объявляются, не выполняются
        names = [
            [
                j.name
                for j in builtin_periodic_jobs(Infrastructure(_settings(), t, sessions=sessions))
            ]
            for t in (plain, with_keys)
        ]
        assert names == [
            ["outbox.purge_published", "inbox.purge"],
            ["outbox.purge_published", "inbox.purge", "idempotency.purge"],
        ]

    def test_service_jobs_are_appended(self) -> None:
        definition = ServiceDefinition(
            name="demo",
            settings_cls=ServiceSettings,
            build_periodic_jobs=lambda _infra: [PeriodicJob("game.schedule", 900, _noop)],
        )
        infra = Infrastructure(_settings(), create_metadata(), sessions=object())  # type: ignore[arg-type]
        assert collect_periodic_jobs(definition, infra)[-1].name == "game.schedule"

    def test_relay_runs_without_kafka_when_jobs_exist(self) -> None:
        tables = create_metadata()
        definition = ServiceDefinition(name="demo", settings_cls=ServiceSettings, tables=tables)
        runtime = Runtime(definition, Command.RELAY, _settings())
        infra = Infrastructure(runtime.settings, tables, sessions=object())  # type: ignore[arg-type]
        assert "relay" in runtime._components(infra, tracing=False)


@pytest.mark.unit
class TestClosers:
    async def test_closers_run_in_reverse_order_and_survive_errors(self) -> None:
        infra = Infrastructure(settings=_settings(), tables=None)
        calls: list[str] = []

        async def first() -> None:
            calls.append("first")

        async def broken() -> None:
            calls.append("broken")
            raise RuntimeError("закрытие сломалось")

        async def last() -> None:
            calls.append("last")

        for closer in (first, broken, last):
            infra.add_closer(closer)
        await infra.close()
        assert calls == ["last", "broken", "first"]
        await infra.close()  # повторный close ничего не вызывает повторно
        assert calls == ["last", "broken", "first"]


# ----------------------------------------------------------------------------- integration
@pytest.fixture
async def infra(database_url: str) -> AsyncIterator[Infrastructure]:
    tables = create_metadata()
    idempotency_table(tables.metadata)
    await create_schema(database_url, tables.metadata)
    opened = await Infrastructure.open(_settings(database_url), tables, clock=FakeClock())
    yield opened
    await opened.close()


@pytest.mark.integration
class TestRunner:
    async def test_two_replicas_run_job_once(self, infra: Infrastructure) -> None:
        assert infra.sessions is not None
        started, release = asyncio.Event(), asyncio.Event()
        runs = 0

        async def slow() -> None:
            nonlocal runs
            runs += 1
            started.set()
            await release.wait()

        job = PeriodicJob("demo.slow", 60, slow)
        replica_a = PeriodicJobRunner("demo", [job], infra.sessions)
        replica_b = PeriodicJobRunner("demo", [job], infra.sessions)
        first = asyncio.create_task(replica_a.run_once("demo.slow"))
        async with asyncio.timeout(5):
            await started.wait()
        assert await replica_b.run_once("demo.slow") is JobResult.SKIPPED
        release.set()
        assert await first is JobResult.OK
        assert runs == 1
        # Блокировка снята вместе с транзакцией — следующий тик снова выполняется.
        assert await replica_b.run_once("demo.slow") is JobResult.OK

    async def test_failure_is_logged_counted_and_loop_continues(
        self, infra: Infrastructure
    ) -> None:
        assert infra.sessions is not None
        calls = 0
        recovered = asyncio.Event()

        async def flaky() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("сбой задачи")
            recovered.set()

        runner = PeriodicJobRunner(
            "demo",
            [PeriodicJob("demo.flaky", 0.01, flaky)],
            infra.sessions,
            random=FakeRandom(values=[0.0]),
        )
        stop = asyncio.Event()
        task = asyncio.create_task(runner.run_forever(stop))
        async with asyncio.timeout(5):
            await recovered.wait()
        stop.set()
        await asyncio.wait_for(task, timeout=5)
        metrics = render_metrics()[0].decode()
        assert 'gv_periodic_job_runs_total{job="demo.flaky",result="error"} 1.0' in metrics
        assert 'gv_periodic_job_runs_total{job="demo.flaky",result="ok"}' in metrics

    async def test_builtin_purge_jobs_delete_only_expired(self, infra: Infrastructure) -> None:
        assert infra.sessions is not None and infra.tables is not None
        tables, clock = infra.tables, infra.clock
        assert isinstance(clock, FakeClock)
        now = clock.now()
        old, fresh = now - timedelta(days=8), now - timedelta(days=1)
        idempotency = tables.metadata.tables["idempotency_keys"]
        async with infra.sessions() as session, session.begin():
            for published_at in (old, fresh, None):
                envelope = EventEnvelope(
                    event_id=uuid7(),
                    event_version=1,
                    event_type="demo.Pinged",
                    producer="demo",
                    aggregate_type="probe",
                    aggregate_id="1",
                    occurred_at=old,
                    payload={},
                )
                await OutboxWriter(tables.outbox).add(session, [envelope])
                if published_at is not None:
                    await session.execute(
                        tables.outbox.update()
                        .where(tables.outbox.c.event_id == envelope.event_id)
                        .values(published_at=published_at)
                    )
            for processed_at in (old, fresh):
                await session.execute(
                    insert(tables.inbox).values(
                        consumer="demo", event_id=uuid7(), processed_at=processed_at
                    )
                )
            for created_at in (now - timedelta(hours=73), now - timedelta(hours=1)):
                await session.execute(
                    insert(idempotency).values(
                        actor_id=uuid7(),
                        key=uuid7().hex,
                        request_hash="0" * 64,
                        response={},
                        status_code=200,
                        created_at=created_at,
                    )
                )

        definition = ServiceDefinition(name="demo", settings_cls=ServiceSettings, tables=tables)
        for name in ("outbox.purge_published", "inbox.purge", "idempotency.purge"):
            assert await run_periodic_job(definition, name, infra) is JobResult.OK

        async with infra.sessions() as session:
            outbox = (await session.execute(select(tables.outbox.c.published_at))).all()
            inbox = await session.scalar(select(func.count()).select_from(tables.inbox))
            keys = await session.scalar(select(func.count()).select_from(idempotency))
        # Неопубликованное событие не удаляется никогда, опубликованное — по сроку.
        assert sorted(str(r.published_at) for r in outbox) == sorted([str(fresh), "None"])
        assert (inbox, keys) == (1, 1)
