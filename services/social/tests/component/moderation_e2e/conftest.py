"""Сквозная фикстура ``real``: настоящий контейнер social (санкции, выдача кейсов, конфиг из БД).

В отличие от ``tests/conftest.py`` здесь нет фейков ``SanctionReader``/``GranterFactory``/
``ConfigProvider``: реальные Postgres и Redis, конфиг публикуется версией в ``config_versions``.
"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr
from social_service.application.config_admin import body_sha256
from social_service.infrastructure.container import SocialContainer
from social_service.infrastructure.container import container_for as social_container_for
from social_service.infrastructure.db.tables import TABLES
from social_service.infrastructure.db.tables_friends import config_versions
from social_service.infrastructure.discussion_container import DiscussionContainer
from social_service.infrastructure.discussion_container import (
    container_for as discussion_container_for,
)
from social_service.main import DEFINITION
from social_service.settings import Settings
from sqlalchemy import select, text

from gv_core.ids import uuid7
from gv_messaging import EventEnvelope, EventProcessor, Inbox, Outcome
from gv_messaging.retry import RetryPolicy
from gv_service import Infrastructure, create_app
from gv_testkit import (
    FakeClock,
    FakeDeadLetterSink,
    InfraProvider,
    TestTokenIssuer,
    apply_migrations,
    load_example,
    outbox_envelopes,
    service_client,
)

EXAMPLE_CONFIG = (
    Path(__file__).resolve().parents[5]
    / "contracts"
    / "social"
    / "examples"
    / "social_config.v1.json"
)
START = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
ADMIN = UUID(int=1)


@dataclass
class Real:
    """Сервис social с настоящим контейнером."""

    client: httpx.AsyncClient
    infra: Infrastructure
    social: SocialContainer
    discussion: DiscussionContainer
    processor: EventProcessor
    issuer: TestTokenIssuer
    clock: FakeClock
    # Обёртка над настоящим granter (сервисы приложения собраны один раз при старте).
    grant_wrapper: Callable[[Any], Any] | None = None

    def auth(
        self, user_id: UUID, perms: tuple[str, ...] = (), sanctions: int = 0
    ) -> dict[str, str]:
        token = self.issuer.issue(user_id=user_id, perms=perms, sanctions=sanctions)
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def key() -> dict[str, str]:
        return {"Idempotency-Key": f"idem-{uuid7().hex}"}

    async def publish_config(self, version: int = 1, **patch: Any) -> dict[str, Any]:
        body: dict[str, Any] = copy.deepcopy(json.loads(EXAMPLE_CONFIG.read_text("utf-8")))
        for dotted, value in patch.items():
            target = body
            *head, last = dotted.split("__")
            for part in head:
                target = target[part]
            target[last] = value
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session, session.begin():
            await session.execute(
                text("UPDATE config_versions SET status='archived' WHERE status='published'")
            )
            await session.execute(
                config_versions.insert().values(
                    version=version,
                    status="published",
                    body=body,
                    body_sha256=body_sha256(body),
                    created_by=ADMIN,
                    created_at=self.clock.now(),
                    published_at=self.clock.now(),
                )
            )
        invalidate = getattr(self.social.config, "invalidate", None)
        if invalidate is not None:
            invalidate()
        return body

    async def deliver(self, envelope: EventEnvelope) -> Outcome:
        return await self.processor.process(envelope.to_bytes(), source="test")

    async def publish_collection(self, author_id: UUID) -> UUID:
        collection_id = uuid7()
        base = load_example("collections.CollectionPublished")
        payload = {
            **base.payload,
            "collection_id": str(collection_id),
            "author_id": str(author_id),
            "version": 1,
            "status": "published",
        }
        outcome = await self.deliver(
            base.model_copy(update={"payload": payload, "event_id": uuid7()})
        )
        assert outcome is Outcome.PROCESSED
        return collection_id

    async def add_nickname(self, user_id: UUID) -> None:
        assert self.infra.sessions is not None
        nick = f"p{user_id.hex[-10:]}"
        async with self.infra.sessions() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO users_proj (user_id, nickname, nickname_key, version, updated_at) "
                    "VALUES (:u, :n, :n, 1, now()) ON CONFLICT (user_id) DO NOTHING"
                ),
                {"u": user_id, "n": nick},
            )

    async def outbox(self, event_type: str | None = None) -> list[EventEnvelope]:
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            events = await outbox_envelopes(session, TABLES.outbox)
        return [e for e in events if event_type is None or e.event_type == event_type]

    async def rows(self, table: Any) -> list[Any]:
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            return list((await session.execute(select(table))).all())


@pytest.fixture
async def real(
    database_url: str, infra: InfraProvider, token_issuer: TestTokenIssuer
) -> AsyncIterator[Real]:
    assert DEFINITION.migrations_path is not None
    assert DEFINITION.build_event_handlers is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    clock = FakeClock(START)
    settings = Settings(
        environment="ci",
        database_url=SecretStr(database_url),
        redis_url=SecretStr(infra.redis_url()),
    )
    runtime = await Infrastructure.open(settings, TABLES, clock=clock)
    assert runtime.redis is not None
    assert runtime.sessions is not None
    await runtime.redis.flushdb()
    discussion = discussion_container_for(runtime)  # без Adapters: всё настоящее
    social = social_container_for(runtime)
    real_factory = discussion.granter_factory
    holder: list[Real] = []

    def granter(uow: Any) -> Any:
        inner = real_factory(uow)
        wrap = holder[0].grant_wrapper if holder else None
        return wrap(inner) if wrap else inner

    discussion.granter_factory = granter

    async def no_sleep(_: float) -> None:
        return None

    processor = EventProcessor(
        group="social.consumer",
        registry=DEFINITION.build_event_handlers(runtime),
        session_factory=runtime.sessions,
        inbox=Inbox(TABLES.inbox),
        dead_letters=FakeDeadLetterSink(),
        clock=clock,
        retry=RetryPolicy(max_attempts=2, base_delay=0.0, max_delay=0.0),
        sleep=no_sleep,
    )
    app = create_app(DEFINITION, runtime, jwt_verifier=token_issuer.verifier())
    try:
        async with service_client(app) as client:
            service = Real(client, runtime, social, discussion, processor, token_issuer, clock)
            holder.append(service)
            await service.publish_config()
            yield service
    finally:
        await runtime.close()
