"""Фикстуры тестов library. Инфраструктура и фейки — из gv_testkit.

Фикстура ``library`` — сервис целиком на реальных Postgres и Redis: HTTP-клиент, consumer
(``EventProcessor``) и outbox; единицы каталога попадают в проекцию событиями catalog.
"""

from __future__ import annotations

import dataclasses
import functools
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from library_service.infrastructure.container import LibraryAdapters
from library_service.infrastructure.db.tables import TABLES
from library_service.infrastructure.limits import RedisRateLimiter
from library_service.infrastructure.shelves_config import load_shelf_catalog
from library_service.main import (
    DEFINITION,
    build_event_handlers,
    build_periodic_jobs,
    build_routers,
)
from library_service.settings import LibraryLimits, LibrarySettings, Settings
from pydantic import SecretStr

from gv_core.ids import uuid7
from gv_messaging import EventEnvelope, EventProcessor, Inbox, Outcome
from gv_service import Infrastructure, JobResult, create_app
from gv_testkit import (
    FakeClock,
    FakeDeadLetterSink,
    InfraProvider,
    TestTokenIssuer,
    apply_migrations,
    load_example,
    outbox_envelopes,
    run_periodic_job,
    service_client,
)
from gv_text.ugc import UgcPolicy

START = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)


@dataclass
class LibraryService:
    """Сервис на реальных Postgres и Redis."""

    client: httpx.AsyncClient
    infra: Infrastructure
    processor: EventProcessor
    issuer: TestTokenIssuer
    clock: FakeClock
    definition: Any

    async def deliver(self, envelope: EventEnvelope) -> Outcome:
        return await self.processor.process(envelope.to_bytes(), source="test")

    async def run_job(self, name: str) -> JobResult:
        return await run_periodic_job(self.definition, name, self.infra)

    async def outbox(self) -> list[EventEnvelope]:
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            return await outbox_envelopes(session, TABLES.outbox)

    def user(self, user_id: UUID | None = None) -> tuple[UUID, dict[str, str]]:
        """Новый пользователь и его заголовки (Bearer)."""
        uid = user_id or uuid7()
        return uid, {"Authorization": f"Bearer {self.issuer.issue(user_id=uid)}"}

    @staticmethod
    def key(value: str | None = None) -> dict[str, str]:
        """``Idempotency-Key`` (новый, если не задан)."""
        return {"Idempotency-Key": value or f"k-{uuid7().hex}"}

    async def save(
        self,
        headers: dict[str, str],
        ref: str,
        item_id: UUID,
        source: str = "reveal",
        idem: str | None = None,
    ) -> httpx.Response:
        """``PUT /v1/shelves/{ref}/items/{id}``."""
        return await self.client.put(
            f"/v1/shelves/{ref}/items/{item_id}",
            json={"source": source},
            headers=headers | self.key(idem),
        )

    async def remove(
        self, headers: dict[str, str], ref: str, item_id: UUID, source: str = "library"
    ) -> httpx.Response:
        """``DELETE /v1/shelves/{ref}/items/{id}``."""
        return await self.client.delete(
            f"/v1/shelves/{ref}/items/{item_id}?source={source}", headers=headers | self.key()
        )

    async def add_item(
        self,
        *,
        domain: str = "film",
        title: str | None = None,
        year: int | None = 2014,
        version: int = 1,
        persons: Sequence[tuple[str, str]] = (("Кристофер Нолан", "director"),),
    ) -> UUID:
        """Единица каталога в проекции (событие ``catalog.MediaItemCreated``)."""
        item_id = uuid7()
        base = load_example("catalog.MediaItemCreated")
        payload = {
            **base.payload,
            "media_item_id": str(item_id),
            "version": version,
            "domain": domain,
            "kind": "track" if domain == "music" else "film",
            "title": title or f"Единица {item_id.hex[-6:]}",
            "year": year,
            "persons": [
                {"person_id": str(uuid7()), "name": name, "role": role, "order": i}
                for i, (name, role) in enumerate(persons)
            ],
        }
        envelope = base.model_copy(update={"aggregate_id": str(item_id), "payload": payload})
        assert await self.deliver(envelope) is Outcome.PROCESSED
        return item_id

    async def set_blocked(self, item_id: UUID, *, blocked: bool, version: int) -> None:
        """``catalog.MediaItemBlocked``/``Unblocked``."""
        name = "catalog.MediaItemBlocked" if blocked else "catalog.MediaItemUnblocked"
        base = load_example(name)
        payload = {**base.payload, "media_item_id": str(item_id), "version": version}
        envelope = base.model_copy(update={"aggregate_id": str(item_id), "payload": payload})
        assert await self.deliver(envelope) is Outcome.PROCESSED


@pytest.fixture
def library_limits() -> LibraryLimits:
    """Лимиты сервиса (тесты лимитов уменьшают их)."""
    return LibraryLimits()


@pytest.fixture
async def library(
    database_url: str,
    infra: InfraProvider,
    token_issuer: TestTokenIssuer,
    library_limits: LibraryLimits,
) -> AsyncIterator[LibraryService]:
    """Сервис library: реальные Postgres и Redis, встроенный конфиг полок и denylist."""
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    settings = Settings(
        database_url=SecretStr(database_url),
        redis_url=SecretStr(infra.redis_url()),
        library=LibrarySettings(limits=library_limits, purge_batch=2),
        public_origins=("https://oncue.test",),
    )
    # Часы стоят на одном моменте, а Redis общий для прогонов: окна лимитов совпадали бы
    # между тестами. Субъект лимита — user_id (uuid7), он уникален в каждом тесте.
    clock = FakeClock(START)
    runtime = await Infrastructure.open(settings, TABLES, clock=clock)
    assert runtime.redis is not None
    adapters = LibraryAdapters(
        catalog=load_shelf_catalog(),
        limiter=RedisRateLimiter(runtime.redis, clock),
        policy=UgcPolicy.from_file(),
    )
    definition = dataclasses.replace(
        DEFINITION,
        build_routers=functools.partial(build_routers, adapters=adapters),
        build_event_handlers=functools.partial(build_event_handlers, adapters=adapters),
        build_periodic_jobs=functools.partial(build_periodic_jobs, adapters=adapters),
    )
    assert runtime.sessions is not None
    assert definition.build_event_handlers is not None
    processor = EventProcessor(
        group="library.consumer",
        registry=definition.build_event_handlers(runtime),
        session_factory=runtime.sessions,
        inbox=Inbox(TABLES.inbox),
        dead_letters=FakeDeadLetterSink(),
        clock=clock,
    )
    app = create_app(definition, runtime, jwt_verifier=token_issuer.verifier())
    try:
        async with service_client(app) as client:
            yield LibraryService(client, runtime, processor, token_issuer, clock, definition)
    finally:
        await runtime.close()
