"""Идемпотентные мутации: ключ и ответ коммитятся в той же транзакции, что и мутация."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Column, Integer, String, Table, Uuid, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from gv_core import ConflictError, uuid7
from gv_db import create_metadata, create_session_factory
from gv_db.idempotency import (
    IdempotencyRecord,
    SqlIdempotencyStore,
    idempotency_table,
    request_fingerprint,
    run_idempotent,
)
from gv_testkit import FakeClock, create_schema

pytestmark = pytest.mark.integration

ACTOR = uuid.UUID("01920000-0000-7000-8000-0000000000a1")


@dataclass
class Env:
    engine: AsyncEngine
    keys: Table
    things: Table

    def sessions(self) -> AsyncSession:
        return create_session_factory(self.engine)()


@pytest.fixture
async def env(database_url: str) -> AsyncIterator[Env]:
    tables = create_metadata()
    keys = idempotency_table(tables.metadata)
    things = Table(
        "things",
        tables.metadata,
        Column("id", Uuid, primary_key=True),
        Column("name", String(50), nullable=False),
        Column("n", Integer, nullable=False, server_default="0"),
    )
    await create_schema(database_url, tables.metadata)
    engine = create_async_engine(database_url)
    yield Env(engine, keys, things)
    await engine.dispose()


async def _count(env: Env, table: Table) -> int:
    async with env.sessions() as s:
        return int(await s.scalar(select(func.count()).select_from(table)) or 0)


async def _wait_for_lock_waiter(env: Env) -> None:
    """Дождаться, пока какой-то запрос этой базы встанет в ожидание блокировки."""
    query = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    async with asyncio.timeout(10), env.engine.connect() as conn:
        # Статистика снимается один раз на транзакцию — каждая итерация в новой.
        while not await conn.scalar(query):
            await conn.rollback()
            await asyncio.sleep(0.02)


async def _create_thing(
    env: Env, clock: FakeClock, key: str, name: str, *, fail: bool = False
) -> tuple[dict[str, Any], bool]:
    """Типовая admin-мутация: изменение + ключ в одной транзакции."""
    async with env.sessions() as session, session.begin():
        store = SqlIdempotencyStore(session, env.keys, clock)

        async def perform() -> dict[str, Any]:
            thing_id = uuid7()
            await session.execute(insert(env.things).values(id=thing_id, name=name))
            if fail:
                raise RuntimeError("бизнес-ошибка после записи")
            return {"id": str(thing_id), "name": name}

        return await run_idempotent(
            store, ACTOR, key, request_fingerprint("thing.create", None, {"name": name}), perform
        )


class TestFingerprint:
    def test_stable_and_key_order_independent(self) -> None:
        target = uuid.UUID(int=1)
        a = request_fingerprint("item.block", target, {"a": 1, "b": [1, 2]})
        b = request_fingerprint("item.block", target, {"b": [1, 2], "a": 1})
        assert a == b
        assert len(a) == 64

    def test_differs_by_action_target_and_body(self) -> None:
        base = request_fingerprint("item.block", uuid.UUID(int=1), {"a": 1})
        assert base != request_fingerprint("item.unblock", uuid.UUID(int=1), {"a": 1})
        assert base != request_fingerprint("item.block", uuid.UUID(int=2), {"a": 1})
        assert base != request_fingerprint("item.block", uuid.UUID(int=1), {"a": 2})

    def test_compatible_with_catalog_admin_hash(self) -> None:
        # Тот же алгоритм, что у catalog (admin_actions.request_hash): переезд без
        # инвалидации уже сохранённых ключей.
        import hashlib
        import json

        target = uuid.UUID(int=7)
        raw = json.dumps(
            {"action": "x", "target": str(target), "body": {"r": "т"}},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        expected = hashlib.sha256(raw.encode()).hexdigest()
        assert request_fingerprint("x", target, {"r": "т"}) == expected


class TestTable:
    def test_factory_is_idempotent_per_metadata(self) -> None:
        md = create_metadata().metadata
        assert idempotency_table(md) is idempotency_table(md)
        assert idempotency_table(md, name="admin_keys").name == "admin_keys"

    async def test_actor_and_key_are_unique(self, env: Env, fake_clock: FakeClock) -> None:
        async with env.sessions() as s, s.begin():
            await SqlIdempotencyStore(s, env.keys, fake_clock).save(
                ACTOR, "key-00001", "h" * 64, {"ok": True}
            )
        # Другой актор с тем же ключом — независимая запись.
        async with env.sessions() as s, s.begin():
            await SqlIdempotencyStore(s, env.keys, fake_clock).save(
                uuid7(), "key-00001", "h" * 64, {"ok": True}
            )
        assert await _count(env, env.keys) == 2


class TestStore:
    async def test_find_returns_saved_record(self, env: Env, fake_clock: FakeClock) -> None:
        async with env.sessions() as s, s.begin():
            store = SqlIdempotencyStore(s, env.keys, fake_clock)
            assert await store.find(ACTOR, "key-00002") is None
            await store.save(ACTOR, "key-00002", "a" * 64, {"x": [1, "т"]}, status_code=201)
        async with env.sessions() as s:
            record = await SqlIdempotencyStore(s, env.keys, fake_clock).find(ACTOR, "key-00002")
        assert record == IdempotencyRecord(
            actor_id=ACTOR,
            key="key-00002",
            request_hash="a" * 64,
            response={"x": [1, "т"]},
            status_code=201,
            created_at=fake_clock.now(),
        )

    async def test_duplicate_save_is_in_progress_conflict(
        self, env: Env, fake_clock: FakeClock
    ) -> None:
        async with env.sessions() as s, s.begin():
            await SqlIdempotencyStore(s, env.keys, fake_clock).save(
                ACTOR, "key-00003", "a" * 64, {}
            )
        async with env.sessions() as s, s.begin():
            store = SqlIdempotencyStore(s, env.keys, fake_clock)
            with pytest.raises(ConflictError) as exc:
                await store.save(ACTOR, "key-00003", "a" * 64, {})
            assert exc.value.code == "idempotency.in_progress"
            # Конфликт откатывает только savepoint: транзакция вызывающего жива.
            assert await store.find(ACTOR, "key-00003") is not None

    async def test_works_on_async_connection(self, env: Env, fake_clock: FakeClock) -> None:
        async with env.engine.begin() as conn:
            store = SqlIdempotencyStore(conn, env.keys, fake_clock)
            await store.save(ACTOR, "key-conn-1", "c" * 64, {"via": "conn"})
            record = await store.find(ACTOR, "key-conn-1")
        assert record is not None and record.response == {"via": "conn"}

    async def test_purge_older_than(self, env: Env, fake_clock: FakeClock) -> None:
        async with env.sessions() as s, s.begin():
            store = SqlIdempotencyStore(s, env.keys, fake_clock)
            await store.save(ACTOR, "key-old-01", "a" * 64, {})
            fake_clock.advance(days=2)
            await store.save(ACTOR, "key-new-01", "a" * 64, {})
        cutoff = fake_clock.now() - timedelta(days=1)
        async with env.sessions() as s, s.begin():
            deleted = await SqlIdempotencyStore(s, env.keys, fake_clock).purge_older_than(cutoff)
        assert deleted == 1
        async with env.sessions() as s:
            store = SqlIdempotencyStore(s, env.keys, fake_clock)
            assert await store.find(ACTOR, "key-old-01") is None
            assert await store.find(ACTOR, "key-new-01") is not None


class TestRunIdempotent:
    async def test_first_call_executes_and_records(self, env: Env, fake_clock: FakeClock) -> None:
        response, executed = await _create_thing(env, fake_clock, "key-run-01", "a")
        assert executed is True
        assert response["name"] == "a"
        assert await _count(env, env.things) == 1
        assert await _count(env, env.keys) == 1

    async def test_repeat_returns_saved_response_without_executing(
        self, env: Env, fake_clock: FakeClock
    ) -> None:
        first, _ = await _create_thing(env, fake_clock, "key-run-02", "a")
        second, executed = await _create_thing(env, fake_clock, "key-run-02", "a")
        assert executed is False
        assert second == first
        assert await _count(env, env.things) == 1

    async def test_same_key_other_request_is_key_reused(
        self, env: Env, fake_clock: FakeClock
    ) -> None:
        await _create_thing(env, fake_clock, "key-run-03", "a")
        with pytest.raises(ConflictError) as exc:
            await _create_thing(env, fake_clock, "key-run-03", "b")
        assert exc.value.code == "idempotency.key_reused"
        assert await _count(env, env.things) == 1

    async def test_failed_mutation_does_not_burn_the_key(
        self, env: Env, fake_clock: FakeClock
    ) -> None:
        # Регресс бага media_pipeline: резерв в отдельной транзакции «залипал» навсегда.
        with pytest.raises(RuntimeError):
            await _create_thing(env, fake_clock, "key-run-04", "a", fail=True)
        assert await _count(env, env.keys) == 0
        assert await _count(env, env.things) == 0
        _, executed = await _create_thing(env, fake_clock, "key-run-04", "a")
        assert executed is True
        assert await _count(env, env.things) == 1

    async def test_rolled_back_transaction_does_not_keep_the_key(
        self, env: Env, fake_clock: FakeClock
    ) -> None:
        async with env.sessions() as session, session.begin():
            store = SqlIdempotencyStore(session, env.keys, fake_clock)

            async def perform() -> dict[str, Any]:
                return {"ok": True}

            await run_idempotent(store, ACTOR, "key-run-05", "f" * 64, perform)
            await session.rollback()
        assert await _count(env, env.keys) == 0

    async def test_concurrent_same_key_executes_once(self, env: Env, fake_clock: FakeClock) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def mutate(name: str) -> tuple[dict[str, Any], bool]:
            async with env.sessions() as session, session.begin():
                store = SqlIdempotencyStore(session, env.keys, fake_clock)

                async def perform() -> dict[str, Any]:
                    await session.execute(insert(env.things).values(id=uuid7(), name=name))
                    return {"name": name}

                fp = request_fingerprint("thing.create", None, {"name": "a"})
                result = await run_idempotent(store, ACTOR, "key-race-1", fp, perform)
                if name == "first":
                    # Ключ первого записан, но не закоммичен — окно гонки.
                    started.set()
                    await release.wait()
                return result

        first = asyncio.create_task(mutate("first"))
        await started.wait()
        second = asyncio.create_task(mutate("second"))
        await _wait_for_lock_waiter(env)  # второй упёрся в незакоммиченный ключ первого
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)

        assert results[0] == ({"name": "first"}, True)
        assert isinstance(results[1], ConflictError)
        assert results[1].code == "idempotency.in_progress"
        assert await _count(env, env.things) == 1

    async def test_response_is_normalized_to_json(self, env: Env, fake_clock: FakeClock) -> None:
        async with env.sessions() as session, session.begin():
            store = SqlIdempotencyStore(session, env.keys, fake_clock)

            async def perform() -> dict[str, Any]:
                return {"items": (1, 2)}

            response, _ = await run_idempotent(store, ACTOR, "key-run-06", "e" * 64, perform)
        # Первый ответ совпадает с тем, что вернётся повтору из JSONB.
        assert response == {"items": [1, 2]}

    async def test_non_json_response_is_rejected(self, env: Env, fake_clock: FakeClock) -> None:
        async with env.sessions() as session, session.begin():
            store = SqlIdempotencyStore(session, env.keys, fake_clock)

            async def perform() -> dict[str, Any]:
                return {"id": uuid7()}

            with pytest.raises(TypeError):
                await run_idempotent(store, ACTOR, "key-run-07", "e" * 64, perform)
