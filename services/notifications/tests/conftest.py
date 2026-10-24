"""Фикстуры тестов notifications. Инфраструктура и фейки - из ``gv_testkit``.

Сборка сервиса на реальных Postgres и Redis: HTTP-клиент, доставка событий через настоящий
``EventProcessor`` (inbox, транзакция), фейки внешних адаптеров (Centrifugo, SMTP, справочник
адресов, очередь задач) и построители входных событий из примеров контракта.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
import pytest
from notifications_service.application.ports import EmailMessage, PermanentEmailError, Recipient
from notifications_service.infrastructure.container import (
    Adapters,
    NotificationsContainer,
    container_for,
)
from notifications_service.infrastructure.db.tables import TABLES
from notifications_service.main import DEFINITION
from notifications_service.settings import NotificationsSettings, Settings
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.sql.expression import TableClause

from gv_core.ids import uuid7
from gv_messaging import EventEnvelope, EventProcessor, Inbox, Outcome
from gv_messaging.ports import TaskQueue
from gv_messaging.retry import RetryPolicy
from gv_service import Infrastructure, create_app
from gv_testkit import (
    FakeClock,
    FakeDeadLetterSink,
    InfraProvider,
    InMemoryTaskQueue,
    TestTokenIssuer,
    apply_migrations,
    load_example,
    service_client,
)

LIMITER_PREFIX = "notifications:unsub:"
ADMIN_PERMS = ("admin.access", "notifications.broadcast.send")


@dataclass
class FakeRealtime:
    """Centrifugo в памяти; ``fail`` имитирует отказ (адаптер не бросает - как настоящий)."""

    published: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail: bool = False

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        if self.fail:
            return
        self.published.append((channel, message))


@dataclass
class FakeEmail:
    """SMTP в памяти; ``fail_for`` - адреса, на которые отправка падает."""

    sent: list[EmailMessage] = field(default_factory=list)
    fail_for: set[str] = field(default_factory=set)
    refuse_for: set[str] = field(default_factory=set)
    """Адреса, отвергнутые окончательно (``PermanentEmailError``)."""
    attempts: list[str] = field(default_factory=list)

    async def send(self, message: EmailMessage) -> None:
        self.attempts.append(message.to)
        if message.to in self.refuse_for:
            raise PermanentEmailError("refused")
        if message.to in self.fail_for:
            raise ConnectionError("smtp down")
        self.sent.append(message)


@dataclass
class FakeDirectory:
    """Справочник адресов: игроки без записи пропускаются."""

    emails: dict[UUID, str] = field(default_factory=dict)

    async def resolve(self, user_ids: list[UUID]) -> list[Recipient]:
        return [Recipient(u, self.emails[u]) for u in user_ids if u in self.emails]


@dataclass
class ForwardingTasks(InMemoryTaskQueue):
    """Очередь в памяти, которая при необходимости пересылает задачи в настоящий брокер."""

    forward: TaskQueue | None = None

    async def enqueue(self, task_type: str, payload: dict[str, Any], *, task_id: str) -> None:
        await super().enqueue(task_type, payload, task_id=task_id)
        if self.forward is not None:
            await self.forward.enqueue(task_type, payload, task_id=task_id)


class NotificationsService:
    """Собранный сервис и помощники тестов."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        infra: Infrastructure,
        container: NotificationsContainer,
        processor: EventProcessor,
        dead_letters: FakeDeadLetterSink,
        issuer: TestTokenIssuer,
        clock: FakeClock,
        realtime: FakeRealtime,
        email: FakeEmail,
        directory: FakeDirectory,
        tasks: ForwardingTasks,
    ) -> None:
        self.client = client
        self.infra = infra
        self.container = container
        self.processor = processor
        self.dead_letters = dead_letters
        self.issuer = issuer
        self.clock = clock
        self.realtime = realtime
        self.email = email
        self.directory = directory
        self.tasks = tasks

    # ------------------------------------------------------------------ запросы
    def auth(self, user_id: UUID | None = None, perms: tuple[str, ...] = ()) -> dict[str, str]:
        token = self.issuer.issue(user_id=user_id or uuid7(), perms=perms)
        return {"Authorization": f"Bearer {token}"}

    def admin(self, user_id: UUID | None = None) -> dict[str, str]:
        return self.auth(user_id, ADMIN_PERMS)

    # ------------------------------------------------------------------ события
    @staticmethod
    def event(event_type: str, **payload: Any) -> EventEnvelope:
        """Пример события контракта с подменой полей payload (новый ``event_id``)."""
        envelope = load_example(event_type)
        return envelope.model_copy(update={"payload": {**envelope.payload, **payload}})

    async def deliver(self, envelope: EventEnvelope) -> Outcome:
        outcome = await self.processor.process(envelope.to_bytes(), source="test")
        await self.container.dispatcher.drain()
        return outcome

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

    async def notify(self, user_id: UUID, **payload: Any) -> None:
        """Создать уведомление «новый уровень» игроку."""
        data = {"user_id": str(user_id), **payload}
        outcome = await self.deliver(self.event("progression.ProgressionLevelChanged", **data))
        assert outcome is Outcome.PROCESSED


@pytest.fixture
async def notifications(
    database_url: str, infra: InfraProvider, token_issuer: TestTokenIssuer, fake_clock: FakeClock
) -> AsyncIterator[NotificationsService]:
    """Сервис notifications: реальные Postgres и Redis, внешние адаптеры - фейки."""
    assert DEFINITION.migrations_path is not None
    assert DEFINITION.build_event_handlers is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    settings = Settings(
        database_url=SecretStr(database_url),
        redis_url=SecretStr(infra.redis_url()),
        notifications=NotificationsSettings(
            unsubscribe_attempts=3,
            unsubscribe_window_s=60,
            broadcast_batch_size=2,
        ),
    )
    runtime = await Infrastructure.open(settings, TABLES, clock=fake_clock)
    realtime, email, directory = FakeRealtime(), FakeEmail(), FakeDirectory()
    tasks = ForwardingTasks()
    container = container_for(
        runtime, Adapters(realtime=realtime, email=email, directory=directory, tasks=tasks)
    )
    assert runtime.sessions is not None
    await _clear_limiter(runtime)
    dead_letters = FakeDeadLetterSink()

    async def no_sleep(_: float) -> None:
        return None

    processor = EventProcessor(
        group="notifications.consumer",
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
            yield NotificationsService(
                client=client,
                infra=runtime,
                container=container,
                processor=processor,
                dead_letters=dead_letters,
                issuer=token_issuer,
                clock=fake_clock,
                realtime=realtime,
                email=email,
                directory=directory,
                tasks=tasks,
            )
    finally:
        await _clear_limiter(runtime)
        await runtime.close()


async def _clear_limiter(runtime: Infrastructure) -> None:
    assert runtime.redis is not None
    async for key in runtime.redis.scan_iter(match=f"{LIMITER_PREFIX}*"):
        await runtime.redis.delete(key)
