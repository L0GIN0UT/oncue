"""Фикстура ``social`` для компонентных тестов друзей (поток F1): реальные Postgres и Redis.

Сервис собирается как в проде (``create_app`` + настоящий ``EventProcessor`` с inbox);
конфиг публикуется из примера контракта; события других сервисов - примеры контрактов.
"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr
from social_service.infrastructure.container import SocialContainer, container_for
from social_service.infrastructure.db.tables import TABLES
from social_service.infrastructure.db.tables_friends import config_versions
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
    FakeRandom,
    InfraProvider,
    TestTokenIssuer,
    apply_migrations,
    load_example,
    outbox_envelopes,
    service_client,
)

EXAMPLE_CONFIG = (
    Path(__file__).resolve().parents[4]
    / "contracts"
    / "social"
    / "examples"
    / "social_config.v1.json"
)
START = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
ADMIN = UUID(int=1)


def config_body(**patch: Any) -> dict[str, Any]:
    """Тело конфига из примера контракта; ``patch`` - вложенные правки через ``__``."""
    raw: dict[str, Any] = copy.deepcopy(json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8")))
    for dotted, value in patch.items():
        target = raw
        *head, last = dotted.split("__")
        for part in head:
            target = target[part]
        target[last] = value
    return raw


@dataclass
class SocialService:
    """Сервис social на реальном Postgres и Redis."""

    client: httpx.AsyncClient
    infra: Infrastructure
    container: SocialContainer
    processor: EventProcessor
    dead_letters: FakeDeadLetterSink
    issuer: TestTokenIssuer
    clock: FakeClock
    nicknames: dict[UUID, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ конфиг
    async def publish_config(self, version: int = 1, **patch: Any) -> None:
        """Опубликовать версию конфига напрямую в БД (предыдущая уходит в архив)."""
        from social_service.application.config_admin import body_sha256

        body = config_body(**patch)
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
        provider = self.container.config
        invalidate = getattr(provider, "invalidate", None)
        if invalidate is not None:
            invalidate()

    # ------------------------------------------------------------------ события и игроки
    async def deliver(self, envelope: EventEnvelope) -> Outcome:
        return await self.processor.process(envelope.to_bytes(), source="test")

    async def register(self, nickname: str, user_id: UUID | None = None, version: int = 1) -> UUID:
        """Игрок с ником: событие ``identity.UserRegistered`` через настоящий процессор."""
        uid = user_id or uuid7()
        base = load_example("identity.UserRegistered")
        payload = {**base.payload, "user_id": str(uid), "nickname": nickname, "version": version}
        outcome = await self.deliver(
            base.model_copy(update={"payload": payload, "aggregate_id": str(uid)})
        )
        assert outcome is Outcome.PROCESSED
        self.nicknames[uid] = nickname
        return uid

    def headers(self, user_id: UUID, *, sanctions: int = 0, guest: bool = False) -> dict[str, str]:
        token = (
            self.issuer.issue_guest(guest_id=user_id)
            if guest
            else self.issuer.issue(user_id=user_id, sanctions=sanctions)
        )
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def key() -> dict[str, str]:
        return {"Idempotency-Key": f"idem-{uuid7().hex}"}

    # ------------------------------------------------------------------ чтение БД
    async def outbox(self, event_type: str | None = None) -> list[EventEnvelope]:
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            events = await outbox_envelopes(session, TABLES.outbox)
        return [e for e in events if event_type is None or e.event_type == event_type]

    async def scalar(self, sql: str, **params: Any) -> Any:
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            return await session.scalar(text(sql).bindparams(**params))

    async def rows(self, table: Any) -> list[Any]:
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            return list((await session.execute(select(table))).all())

    # ------------------------------------------------------------------ сценарии
    async def befriend(self, a: UUID, b: UUID) -> None:
        """Подружить через API: заявка и принятие."""
        sent = await self.client.post(
            "/v1/me/friend-requests",
            json={"nickname": self.nicknames[b]},
            headers={**self.headers(a), **self.key()},
        )
        assert sent.status_code == 201, sent.text
        accepted = await self.client.post(
            f"/v1/me/friend-requests/{sent.json()['id']}/accept",
            headers={**self.headers(b), **self.key()},
        )
        assert accepted.status_code == 200, accepted.text


@pytest.fixture
def fake_clock_start() -> FakeClock:
    return FakeClock(START)


@pytest.fixture
async def social(
    database_url: str,
    infra: InfraProvider,
    token_issuer: TestTokenIssuer,
    fake_clock_start: FakeClock,
) -> AsyncIterator[SocialService]:
    """Сервис social: реальный Postgres и Redis, события через EventProcessor."""
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    settings = Settings(
        environment="ci",
        database_url=SecretStr(database_url),
        redis_url=SecretStr(infra.redis_url()),
    )
    runtime = await Infrastructure.open(settings, TABLES, clock=fake_clock_start)
    assert runtime.redis is not None
    await runtime.redis.flushdb()  # счётчики и присутствие прошлых тестов общего Redis
    container = container_for(runtime)
    container.rng = FakeRandom(seed=7)
    assert runtime.sessions is not None
    assert DEFINITION.build_event_handlers is not None
    dead_letters = FakeDeadLetterSink()

    async def no_sleep(_: float) -> None:
        return None

    processor = EventProcessor(
        group="social.consumer",
        registry=DEFINITION.build_event_handlers(runtime),
        session_factory=runtime.sessions,
        inbox=Inbox(TABLES.inbox),
        dead_letters=dead_letters,
        clock=fake_clock_start,
        retry=RetryPolicy(max_attempts=2, base_delay=0.0, max_delay=0.0),
        sleep=no_sleep,
    )
    app = create_app(DEFINITION, runtime, jwt_verifier=token_issuer.verifier())
    try:
        async with service_client(app) as client:
            yield SocialService(
                client, runtime, container, processor, dead_letters, token_issuer, fake_clock_start
            )
    finally:
        await runtime.close()
