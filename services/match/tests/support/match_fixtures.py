"""Фикстуры сервиса match целиком (component и contract; сборка — ``match_app.py``)."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import functools
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from match_app import START, MatchApp, MemoryModes, settings
from match_doubles import plan_factory, seed_bodies
from match_service.infrastructure.container import MatchAdapters
from match_service.infrastructure.db.tables import TABLES
from match_service.main import (
    DEFINITION,
    build_event_handlers,
    build_periodic_jobs,
    build_routers,
    build_runners,
)
from redis.asyncio import Redis

from gv_core.ids import uuid7
from gv_core.random import SystemRandom
from gv_messaging import EventProcessor, Inbox
from gv_service import Infrastructure, create_app, supervise_runners
from gv_service.settings import RunnersSettings
from gv_testkit import (
    FakeClock,
    FakeDeadLetterSink,
    InfraProvider,
    TestTokenIssuer,
    apply_migrations,
    service_client,
)
from gv_testkit.match import FakeLinkPort, FakeRealtime, FakeRoundPlanPort


@pytest.fixture
def redis_url(infra: InfraProvider) -> str:
    return infra.redis_url()


@pytest.fixture
async def migrated_url(database_url: str) -> str:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    return database_url


AppFactory = Callable[..., Awaitable[MatchApp]]


class SlowPlans(FakeRoundPlanPort):
    """План game, который отвечает дольше ``GV_MATCH__REPLY_TIMEOUT_MS`` (старт — 202)."""

    def __init__(self, delay_s: float) -> None:
        super().__init__()
        self._delay_s = delay_s

    async def create_round_plan(self, request: Any) -> Any:
        await asyncio.sleep(self._delay_s)
        return await super().create_round_plan(request)


@pytest.fixture
async def app_factory(
    migrated_url: str, redis_url: str, token_issuer: TestTokenIssuer
) -> AsyncIterator[AppFactory]:
    """Фабрика сервиса: ``await factory(rooms=True, **GV_MATCH__)``."""
    stack = contextlib.AsyncExitStack()
    flush = Redis.from_url(redis_url)
    await flush.flushdb()
    await flush.aclose()

    async def build(*, rooms: bool = True, plan_delay_s: float = 0, **match: Any) -> MatchApp:
        clock = FakeClock(START)
        infra = await Infrastructure.open(settings(migrated_url, redis_url, **match), TABLES, clock)
        stack.push_async_callback(infra.close)
        bodies = seed_bodies()
        plans = SlowPlans(plan_delay_s) if plan_delay_s else FakeRoundPlanPort()
        plans._factory = plan_factory(bodies)
        links = FakeLinkPort(now=START, ttl_s=3600)
        realtime = FakeRealtime()
        adapters = MatchAdapters(
            plans=plans,
            assets=links,
            publisher=realtime,
            modes=MemoryModes(bodies),
            random=SystemRandom(),
            instance=f"test-{uuid7().hex[-8:]}",
        )
        definition = dataclasses.replace(
            DEFINITION,
            build_routers=functools.partial(build_routers, adapters=adapters),
            build_event_handlers=functools.partial(build_event_handlers, adapters=adapters),
            build_periodic_jobs=functools.partial(build_periodic_jobs, adapters=adapters),
            build_runners=functools.partial(build_runners, adapters=adapters),
        )
        assert definition.build_event_handlers is not None and infra.sessions is not None
        processor = EventProcessor(
            group="match.consumer",
            registry=definition.build_event_handlers(infra),
            session_factory=infra.sessions,
            inbox=Inbox(TABLES.inbox),
            dead_letters=FakeDeadLetterSink(),
            clock=clock,
        )
        app = create_app(definition, infra, jwt_verifier=token_issuer.verifier())
        client = await stack.enter_async_context(service_client(app))
        assert infra.redis is not None
        service = MatchApp(
            client, infra, clock, token_issuer, plans, links, realtime, processor, infra.redis
        )
        if rooms:
            stop = asyncio.Event()
            assert definition.build_runners is not None
            runners = definition.build_runners(infra)
            task = asyncio.create_task(
                supervise_runners(runners, stop, RunnersSettings(stop_timeout_s=2))
            )

            async def shutdown() -> None:
                stop.set()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(task, 5)

            stack.push_async_callback(shutdown)
        return service

    async with stack:
        yield build


@pytest.fixture
async def svc(app_factory: AppFactory) -> MatchApp:
    """Сервис match с процессом rooms."""
    return await app_factory()
