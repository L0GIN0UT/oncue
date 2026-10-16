"""Адаптеры library на реальных Postgres и Redis: гонки мутаций, лимитер, удаление порциями."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from library_service.domain.errors import LibraryRateLimitedError
from library_service.infrastructure.container import container_for
from library_service.infrastructure.db.repositories import SqlLibraryUnitOfWork
from library_service.infrastructure.limits import RedisRateLimiter
from library_service.settings import LibraryLimits
from redis.asyncio import Redis

from gv_core.ids import uuid7
from gv_testkit import FakeClock, InfraProvider

pytestmark = pytest.mark.integration


async def test_concurrent_first_saves_create_one_system_shelf(library: Any) -> None:
    """Блокировка ``user_versions`` упорядочивает мутации: ленивое создание полки не
    упирается в UNIQUE ``(user_id, key)``, версии событий не повторяются."""
    _, me = library.user()
    items = [await library.add_item() for _ in range(6)]
    responses = await asyncio.gather(*(library.save(me, "system:favorite", i) for i in items))
    assert [r.status_code for r in responses] == [200] * 6
    shelves = (await library.client.get("/v1/shelves", headers=me)).json()
    assert shelves[2]["items_count"] == 6
    versions = [e.payload["version"] for e in await library.outbox() if e.producer == "library"]
    assert sorted(versions) == list(range(1, 7))


@pytest.mark.parametrize("library_limits", [LibraryLimits(items_per_shelf_max=3)])
async def test_concurrent_saves_respect_shelf_limit(library: Any) -> None:
    _, me = library.user()
    items = [await library.add_item() for _ in range(6)]
    responses = await asyncio.gather(*(library.save(me, "system:favorite", i) for i in items))
    assert sorted(r.status_code for r in responses) == [200] * 3 + [409] * 3
    shelves = (await library.client.get("/v1/shelves", headers=me)).json()
    assert shelves[2]["items_count"] == 3


async def test_redis_rate_limiter_window(infra: InfraProvider) -> None:
    redis = Redis.from_url(infra.redis_url())
    try:
        limiter = RedisRateLimiter(redis, FakeClock())
        subject = f"user-{uuid7().hex}"
        for _ in range(3):
            await limiter.hit("items", subject, limit=3, window_s=60)
        with pytest.raises(LibraryRateLimitedError) as exc:
            await limiter.hit("items", subject, limit=3, window_s=60)
        assert exc.value.details["retry_after"] >= 1
        await limiter.hit("shelves", subject, limit=3, window_s=60)  # другой bucket
    finally:
        await redis.aclose()


async def test_purge_batches_are_bounded_and_idempotent(library: Any) -> None:
    user_id, me = library.user()
    for _ in range(5):
        await library.save(me, "system:favorite", await library.add_item())
    container = container_for(library.infra)
    factory = container.uow_factory()
    async with factory() as uow:
        assert isinstance(uow, SqlLibraryUnitOfWork)
        await uow.purges.request(user_id, library.clock.now())
        await uow.purges.request(user_id, library.clock.now())  # повтор — no-op
        assert await uow.purges.pending(10) == [user_id]
        assert await uow.purges.delete_batch(user_id, 2) == 2
        await uow.commit()
    # Остальное: 2 + 1 единица, затем полка, версия и ключи идемпотентности, затем 0.
    deleted = await container.purge.run()
    assert deleted == 3 + 1 + 1 + 5
    async with factory() as uow:
        assert await uow.purges.pending(10) == []
        assert await uow.purges.delete_batch(user_id, 2) == 0
