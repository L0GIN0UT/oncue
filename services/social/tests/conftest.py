"""Фикстуры тестов social. Инфраструктура и фейки - из gv_testkit.

Двойники портов самого social лежат в ``tests/support`` (каталог добавляется в ``sys.path``;
имена модулей уникальны, чтобы не пересечься с другими сервисами).

Фикстура ``social`` собирает сервис на реальных Postgres и Redis: HTTP-клиент, доставка
событий через настоящий ``EventProcessor`` (inbox, транзакция), конфиг из примера контракта.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest

_SUPPORT = str(Path(__file__).parent / "support")
if _SUPPORT not in sys.path:
    sys.path.insert(0, _SUPPORT)

from pydantic import SecretStr  # noqa: E402
from social_fakes import FakeConfig  # noqa: E402
from social_service.infrastructure.container import (  # noqa: E402
    container_for as social_container_for,
)
from social_service.infrastructure.db import tables_moderation as t  # noqa: E402
from social_service.infrastructure.db.tables import TABLES  # noqa: E402
from social_service.infrastructure.discussion_container import (  # noqa: E402
    Adapters,
    DiscussionContainer,
    container_for,
)
from social_service.main import DEFINITION  # noqa: E402
from social_service.settings import Settings  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.sql.expression import TableClause  # noqa: E402

from gv_core.ids import uuid7  # noqa: E402
from gv_messaging import EventEnvelope, EventProcessor, Inbox, Outcome  # noqa: E402
from gv_messaging.retry import RetryPolicy  # noqa: E402
from gv_service import Infrastructure, create_app  # noqa: E402
from gv_testkit import (  # noqa: E402
    FakeClock,
    FakeDeadLetterSink,
    InfraProvider,
    TestTokenIssuer,
    apply_migrations,
    load_example,
    service_client,
)

KEY_COUNTER = iter(range(10**9))


class SocialService:
    """Собранный сервис и помощники тестов."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        infra: Infrastructure,
        container: DiscussionContainer,
        processor: EventProcessor,
        dead_letters: FakeDeadLetterSink,
        issuer: TestTokenIssuer,
        clock: FakeClock,
        config: FakeConfig,
    ) -> None:
        self.client = client
        self.infra = infra
        self.container = container
        self.processor = processor
        self.dead_letters = dead_letters
        self.issuer = issuer
        self.clock = clock
        self.config = config

    # ------------------------------------------------------------------ запросы
    def auth(
        self, user_id: UUID | None = None, perms: tuple[str, ...] = (), sanctions: int = 0
    ) -> dict[str, str]:
        token = self.issuer.issue(user_id=user_id or uuid7(), perms=perms, sanctions=sanctions)
        return {"Authorization": f"Bearer {token}"}

    def guest(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.issuer.issue_guest()}"}

    @staticmethod
    def key() -> dict[str, str]:
        return {"Idempotency-Key": f"idem-{next(KEY_COUNTER):016d}"}

    # ------------------------------------------------------------------ события
    @staticmethod
    def event(event_type: str, *, fresh: bool = True, **payload: Any) -> EventEnvelope:
        """Пример события контракта с подменой полей payload (по умолчанию новый ``event_id``)."""
        envelope = load_example(event_type)
        update: dict[str, Any] = {"payload": {**envelope.payload, **payload}}
        if fresh:
            update["event_id"] = uuid7()
        return envelope.model_copy(update=update)

    async def deliver(self, envelope: EventEnvelope) -> Outcome:
        return await self.processor.process(envelope.to_bytes(), source="test")

    async def publish_collection(
        self, author_id: UUID, *, version: int = 1, status: str = "published"
    ) -> UUID:
        """Сборник появляется в проекции так же, как в бою - событием collections."""
        collection_id = uuid7()
        outcome = await self.deliver(
            self.event(
                "collections.CollectionPublished",
                collection_id=str(collection_id),
                author_id=str(author_id),
                version=version,
                status=status,
            )
        )
        assert outcome is Outcome.PROCESSED
        return collection_id

    async def add_nickname(self, user_id: UUID, nickname: str) -> None:
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO users_proj (user_id, nickname, nickname_key, version, updated_at) "
                    "VALUES (:u, :n, :k, 1, now()) "
                    "ON CONFLICT (user_id) DO UPDATE SET nickname = :n, nickname_key = :k"
                ),
                {"u": user_id, "n": nickname, "k": nickname.lower()},
            )

    async def add_participants(self, match_id: UUID, *users: UUID, solo: bool = False) -> None:
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session, session.begin():
            for user in users:
                await session.execute(
                    t.match_participants_proj.insert().values(
                        match_id=match_id, user_id=user, solo=solo, finished_at=self.clock.now()
                    )
                )

    # ------------------------------------------------------------------ чтение БД
    async def count(self, table: TableClause, *where: Any) -> int:
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            stmt = select(func.count()).select_from(table)
            for clause in where:
                stmt = stmt.where(clause)
            return int(await session.scalar(stmt) or 0)

    async def rows(self, table: TableClause, *where: Any) -> list[Any]:
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            stmt = select(table)
            for clause in where:
                stmt = stmt.where(clause)
            return list((await session.execute(stmt)).all())

    async def outbox_events(self, event_type: str) -> list[EventEnvelope]:
        from gv_testkit import outbox_envelopes

        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            return [
                e
                for e in await outbox_envelopes(session, TABLES.outbox)
                if e.event_type == event_type
            ]


@pytest.fixture
async def social(
    database_url: str, infra: InfraProvider, token_issuer: TestTokenIssuer, fake_clock: FakeClock
) -> AsyncIterator[SocialService]:
    """Сервис social: реальные Postgres и Redis, конфиг - из примера контракта."""
    assert DEFINITION.migrations_path is not None
    assert DEFINITION.build_event_handlers is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    settings = Settings(
        database_url=SecretStr(database_url), redis_url=SecretStr(infra.redis_url())
    )
    runtime = await Infrastructure.open(settings, TABLES, clock=fake_clock)
    assert runtime.sessions is not None
    config = FakeConfig()
    container = container_for(runtime, Adapters(config=config))
    # Санкции берут конфиг из контейнера друзей: подменяем его тем же двойником.
    social_container_for(runtime).config = config  # type: ignore[assignment]  # cached_property
    dead_letters = FakeDeadLetterSink()

    async def no_sleep(_: float) -> None:
        return None

    processor = EventProcessor(
        group="social.consumer",
        registry=DEFINITION.build_event_handlers(runtime),
        session_factory=runtime.sessions,
        inbox=Inbox(TABLES.inbox),
        dead_letters=dead_letters,
        clock=fake_clock,
        retry=RetryPolicy(max_attempts=2, base_delay=0.0, max_delay=0.0),
        sleep=no_sleep,
    )
    app = create_app(DEFINITION, runtime, jwt_verifier=token_issuer.verifier())
    try:
        async with service_client(app) as client:
            yield SocialService(
                client=client,
                infra=runtime,
                container=container,
                processor=processor,
                dead_letters=dead_letters,
                issuer=token_issuer,
                clock=fake_clock,
                config=config,
            )
    finally:
        await runtime.close()
