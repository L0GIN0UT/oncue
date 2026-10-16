"""Фикстуры тестов collections. Инфраструктура и фейки — из ``gv_testkit``.

Здесь — то, чего нет в testkit: сборка сервиса на реальных Postgres и Redis (фикстура
``svc``) с доставкой событий через ``EventProcessor`` и помощники сценариев (единицы
каталога, публикация сборника, завершение игры).
"""

from __future__ import annotations

import dataclasses
import functools
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from collections_service.application.ports import RateLimiter
from collections_service.infrastructure.container import CollectionsAdapters
from collections_service.infrastructure.db.tables import TABLES
from collections_service.infrastructure.limits import RedisRateLimiter
from collections_service.main import (
    DEFINITION,
    build_event_handlers,
    build_periodic_jobs,
    build_routers,
)
from collections_service.settings import CollectionsLimits, CollectionsSettings, Settings
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

START = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
ADMIN_PERMS = (
    "admin.access",
    "collections.view",
    "collections.curate",
    "collections.unpublish",
    "collections.restrict_author",
)


def _key() -> dict[str, str]:
    return {"Idempotency-Key": f"test-{uuid7().hex}"}


@dataclass
class User:
    """Пользователь теста: id и заголовок авторизации."""

    user_id: UUID
    headers: dict[str, str]

    def mutate(self, idem: str | None = None) -> dict[str, str]:
        """Заголовки мутации (свой ключ на каждый вызов, если не задан)."""
        return self.headers | ({"Idempotency-Key": idem} if idem else _key())


@dataclass
class CollectionsService:
    """Сервис на реальных Postgres и Redis."""

    client: httpx.AsyncClient
    infra: Infrastructure
    processor: EventProcessor
    issuer: TestTokenIssuer
    clock: FakeClock
    definition: Any

    # ------------------------------------------------------------------ субъекты
    def user(self, *, sanctions: int = 0, perms: Sequence[str] = ()) -> User:
        """Новый пользователь (уникальный id: лимиты Redis не пересекаются между тестами)."""
        user_id = uuid7()
        token = self.issuer.issue(user_id=user_id, sanctions=sanctions, perms=perms)
        return User(user_id, {"Authorization": f"Bearer {token}"})

    def guest(self) -> User:
        """Гость."""
        guest_id = uuid7()
        return User(
            guest_id, {"Authorization": f"Bearer {self.issuer.issue_guest(guest_id=guest_id)}"}
        )

    def admin(self, *perms: str) -> User:
        """Админ с правами (по умолчанию — все права сборников)."""
        return self.user(perms=perms or ADMIN_PERMS)

    # ------------------------------------------------------------------ события
    async def deliver(self, envelope: EventEnvelope) -> Outcome:
        """Доставить событие consumer'у."""
        return await self.processor.process(envelope.to_bytes(), source="test")

    async def outbox(self) -> list[EventEnvelope]:
        """События outbox по порядку."""
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            return await outbox_envelopes(session, TABLES.outbox)

    async def events(self, event_type: str, collection_id: str | UUID) -> list[EventEnvelope]:
        """События сборника данного типа."""
        return [
            e
            for e in await self.outbox()
            if e.event_type == event_type and e.aggregate_id == str(collection_id)
        ]

    async def run_job(self, name: str) -> JobResult:
        """Периодическая задача (как в relay)."""
        return await run_periodic_job(self.definition, name, self.infra)

    async def sql(self, statement: str, **params: Any) -> list[Any]:
        """Прочитать строки напрямую (проверки состояния БД)."""
        from sqlalchemy import text

        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            return list((await session.execute(text(statement), params)).all())

    async def sql_exec(self, statement: str, **params: Any) -> None:
        """Изменить строки напрямую (состояние, которого не достичь через API)."""
        from sqlalchemy import text

        assert self.infra.sessions is not None
        async with self.infra.sessions() as session, session.begin():
            await session.execute(text(statement), params)

    # ------------------------------------------------------------------ каталог
    async def add_item(
        self, *, domain: str = "film", title: str | None = None, year: int = 2001
    ) -> UUID:
        """Единица каталога (событие ``catalog.MediaItemCreated``)."""
        item_id = uuid7()
        base = load_example("catalog.MediaItemCreated")
        payload = {
            **base.payload,
            "media_item_id": str(item_id),
            "version": 1,
            "domain": domain,
            "kind": "track" if domain == "music" else "film",
            "title": title or f"Единица {item_id.hex[-6:]}",
            "year": year,
            "status": "active",
        }
        envelope = base.model_copy(update={"aggregate_id": str(item_id), "payload": payload})
        assert await self.deliver(envelope) is Outcome.PROCESSED
        return item_id

    async def add_items(self, n: int, *, domain: str = "film") -> list[UUID]:
        """``n`` единиц каталога."""
        return [await self.add_item(domain=domain) for _ in range(n)]

    async def block_item(self, item_id: UUID, version: int = 2) -> Outcome:
        """``catalog.MediaItemBlocked``."""
        base = load_example("catalog.MediaItemBlocked")
        payload = {**base.payload, "media_item_id": str(item_id), "version": version}
        return await self.deliver(
            base.model_copy(update={"aggregate_id": str(item_id), "payload": payload})
        )

    # ------------------------------------------------------------------ сценарии
    async def create(
        self, author: User, *, domain: str = "film", title: str = "Кино · кадры"
    ) -> dict[str, Any]:
        """Создать черновик (201)."""
        response = await self.client.post(
            "/v1/collections", json={"domain": domain, "title": title}, headers=author.mutate()
        )
        assert response.status_code == 201, response.text
        data: dict[str, Any] = response.json()
        return data

    async def save(
        self, author: User, collection_id: str, body: dict[str, Any], *, base: int
    ) -> httpx.Response:
        """Сохранить черновик."""
        return await self.client.put(
            f"/v1/collections/{collection_id}/draft",
            json={"base_revision": base, "body": body},
            headers=author.mutate(),
        )

    async def publish(self, author: User, collection_id: str, revision: int) -> httpx.Response:
        """Опубликовать ревизию."""
        return await self.client.post(
            f"/v1/collections/{collection_id}/publish",
            json={"draft_revision": revision},
            headers=author.mutate(),
        )

    async def published(
        self,
        author: User,
        *,
        items: Sequence[UUID] | None = None,
        domain: str = "film",
        title: str = "Кино · кадры",
        **extra: Any,
    ) -> dict[str, Any]:
        """Опубликованный сборник: ``CollectionDetail`` ответа публикации."""
        ids = list(items) if items is not None else await self.add_items(5, domain=domain)
        draft = await self.create(author, domain=domain, title=title)
        body = {
            "title": title,
            "default_mode_key": f"{domain}.collection.frame",
            "items": [{"media_item_id": str(i)} for i in ids],
            **extra,
        }
        saved = await self.save(author, draft["collection_id"], body, base=draft["revision"])
        assert saved.status_code == 200, saved.text
        response = await self.publish(author, draft["collection_id"], saved.json()["revision"])
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        return data

    async def finish_game(
        self,
        user: User | None,
        collection_id: str,
        *,
        version: int = 1,
        score: int = 17,
        max_score: int = 24,
        guest: bool = False,
    ) -> Outcome:
        """``game.GameFinished`` по сборнику."""
        base = load_example("game.GameFinished", variant="collection")
        payload = {
            **base.payload,
            "session_id": str(uuid7()),
            "user_id": None if guest or user is None else str(user.user_id),
            "is_guest": guest,
            "collection_id": collection_id,
            "collection_version": version,
            "score": score,
            "max_score": max_score,
            "finished_at": self.clock.now().isoformat(),
        }
        envelope = base.model_copy(
            update={"aggregate_id": payload["session_id"], "payload": payload}
        )
        return await self.deliver(envelope)


def _settings(database_url: str, redis_url: str, **limits: int) -> Settings:
    """Настройки сервиса для тестов (поиск без лимита: Redis общий для прогонов)."""
    return Settings(
        database_url=SecretStr(database_url),
        redis_url=SecretStr(redis_url),
        public_origins=("https://oncue.test",),
        collections=CollectionsSettings(
            limits=CollectionsLimits(**({"search_per_min": 100_000} | limits))
        ),
    )


async def open_service(
    settings: Settings,
    issuer: TestTokenIssuer,
    clock: FakeClock,
    limiter: RateLimiter | None = None,
) -> tuple[Infrastructure, Any, EventProcessor, Any]:
    """Инфраструктура, описание сервиса с адаптерами, процессор событий, приложение."""
    runtime = await Infrastructure.open(settings, TABLES, clock=clock)
    assert runtime.redis is not None and runtime.sessions is not None
    adapters = CollectionsAdapters(limiter=limiter or RedisRateLimiter(runtime.redis, clock))
    definition = dataclasses.replace(
        DEFINITION,
        build_routers=functools.partial(build_routers, adapters=adapters),
        build_event_handlers=functools.partial(build_event_handlers, adapters=adapters),
        build_periodic_jobs=functools.partial(build_periodic_jobs, adapters=adapters),
    )
    assert definition.build_event_handlers is not None
    processor = EventProcessor(
        group="collections.consumer",
        registry=definition.build_event_handlers(runtime),
        session_factory=runtime.sessions,
        inbox=Inbox(TABLES.inbox),
        dead_letters=FakeDeadLetterSink(),
        clock=clock,
    )
    app = create_app(definition, runtime, jwt_verifier=issuer.verifier())
    return runtime, definition, processor, app


@pytest.fixture
def redis_url(infra: InfraProvider) -> str:
    """Тестовый Redis."""
    return infra.redis_url()


@pytest.fixture
async def migrated_url(database_url: str) -> str:
    """Временная база с миграциями сервиса."""
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    return database_url


ServiceFactory = Callable[..., Awaitable[CollectionsService]]


@pytest.fixture
async def service_factory(
    migrated_url: str, redis_url: str, token_issuer: TestTokenIssuer
) -> AsyncIterator[ServiceFactory]:
    """Фабрика сервиса с другими лимитами или лимитером: ``await factory(limiter, **limits)``."""
    stack = AsyncExitStack()

    async def build(limiter: RateLimiter | None = None, **limits: int) -> CollectionsService:
        clock = FakeClock(START)
        runtime, definition, processor, app = await open_service(
            _settings(migrated_url, redis_url, **limits), token_issuer, clock, limiter
        )
        stack.push_async_callback(runtime.close)
        client = await stack.enter_async_context(service_client(app))
        return CollectionsService(client, runtime, processor, token_issuer, clock, definition)

    async with stack:
        yield build


@pytest.fixture
async def svc(service_factory: ServiceFactory) -> CollectionsService:
    """Сервис collections: реальные Postgres и Redis, часы теста."""
    return await service_factory()
