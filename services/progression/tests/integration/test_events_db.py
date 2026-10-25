"""Сезонные события на реальных Postgres и Redis: гонки, схема, откат миграции (ADR 0054-0056)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from alembic import command
from progression_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from gv_core.ids import uuid7
from gv_db.migrations import alembic_config
from gv_testkit import assert_problem

pytestmark = pytest.mark.integration

Service = Any  # фикстура `progression` описана в conftest.py
KEY = "autumn"
PARALLEL = 12


async def test_parallel_claims_give_one_reward(progression: Service) -> None:
    ev = progression.ev
    await progression.publish_config()
    await progression.publish_event(KEY, ev.body(ev.find("leaf", 1), ev.find("fin", 2, final=True)))
    user = uuid7()
    responses = await asyncio.gather(
        *(progression.claim(user, KEY, "leaf") for _ in range(PARALLEL))
    )
    assert {r.status_code for r in responses} == {200}
    assert sum(r.json()["newly_unlocked"] for r in responses) == 1
    rows = [r for r in await progression.rows("xp_ledger", user) if r.reason == "event"]
    assert len(rows) == 1
    assert await progression.verse_rows(user) == 1
    assert len(await progression.outbox("progression.FindUnlocked")) == 1


async def test_parallel_server_events_give_one_reward(progression: Service) -> None:
    ev = progression.ev
    await progression.publish_config(at=progression.clock.now() - timedelta(days=60))
    await progression.publish_event(
        KEY,
        ev.body(ev.find("r", 1, ev.trigger("rounds_played", 1)), ev.find("f", 2, final=True)),
    )
    user = uuid7()
    envelopes = [
        progression.game_finished(user, [10]).model_copy(
            update={"event_id": uuid7(), "occurred_at": progression.clock.now()}
        )
        for _ in range(PARALLEL)
    ]
    await asyncio.gather(*(progression.deliver(e) for e in envelopes))
    rows = [r for r in await progression.rows("xp_ledger", user) if r.reason == "event"]
    assert len(rows) == 1
    assert len(await progression.outbox("progression.FindUnlocked")) == 1


async def test_parallel_publish_of_overlapping_runs_lets_only_one_through(
    progression: Service,
) -> None:
    ev = progression.ev
    await progression.publish_config()
    body = ev.body(ev.find("a", 1), ev.find("f", 2, final=True))
    runs: list[tuple[str, str, str]] = []
    now = progression.clock.now()
    for key in ("one", "two"):
        await progression.create_event(key, body)
        run = await progression.client.post(
            f"/admin/v1/events/{key}/runs",
            headers=progression.admin(ev.MANAGE),
            json={
                "starts_at": now.isoformat(),
                "ends_at": (now + timedelta(days=3)).isoformat(),
                "reason": "тест гонки",
            },
        )
        event = await progression.client.get(
            f"/admin/v1/events/{key}", headers=progression.admin(ev.MANAGE)
        )
        runs.append((key, run.json()["id"], event.json()["body_sha256"]))

    async def publish(key: str, run: str, sha: str) -> int:
        response = await progression.client.post(
            f"/admin/v1/events/{key}/runs/{run}/publish",
            headers=progression.admin(ev.PUBLISH),
            json={"body_sha256": sha, "reason": "тест гонки"},
        )
        return response.status_code

    statuses = await asyncio.gather(*(publish(*r) for r in runs))
    assert sorted(statuses) == [200, 409]


async def test_snapshot_is_immutable_for_players_after_definition_edit(
    progression: Service,
) -> None:
    ev = progression.ev
    await progression.publish_config()
    run = await progression.publish_event(
        KEY, ev.body(ev.find("leaf", 1), ev.find("fin", 2, final=True))
    )
    await progression.client.post(
        f"/admin/v1/events/{KEY}/runs/{run}/stop",
        headers=progression.admin(ev.PUBLISH),
        json={"reason": "тест"},
    )
    changed = ev.body(ev.find("other", 1), ev.find("fin2", 2, final=True))
    await progression.client.put(
        f"/admin/v1/events/{KEY}",
        headers=progression.admin(ev.MANAGE),
        json={"body": changed, "reason": "правка"},
    )
    page = (await progression.get(f"/v1/events/{KEY}", uuid7())).json()
    assert [f["key"] for f in page["finds"]] == ["leaf", "fin"]


async def test_schema_forbids_second_unlock_and_dangling_rows(progression: Service) -> None:
    ev = progression.ev
    await progression.publish_config()
    run = await progression.publish_event(
        KEY, ev.body(ev.find("leaf", 1), ev.find("fin", 2, final=True))
    )
    assert progression.infra.sessions is not None
    user = uuid7()
    insert = text(
        "INSERT INTO event_find_progress (run_id, user_id, find_key, period_key, progress, "
        "unlocked_at, updated_at) VALUES (:r, :u, 'leaf', :p, 1, now(), now())"
    )
    async with progression.infra.sessions() as session, session.begin():
        await session.execute(insert, {"r": run, "u": user, "p": "2026-10-23"})
    with pytest.raises(IntegrityError):  # вторая открытая строка той же находки
        async with progression.infra.sessions() as session, session.begin():
            await session.execute(insert, {"r": run, "u": user, "p": "2026-10-24"})
    with pytest.raises(IntegrityError):  # запуска нет
        async with progression.infra.sessions() as session, session.begin():
            await session.execute(insert, {"r": uuid7(), "u": uuid7(), "p": "event"})
    with pytest.raises(IntegrityError):  # запуск с прогрессом не удалить (RESTRICT)
        async with progression.infra.sessions() as session, session.begin():
            await session.execute(text("DELETE FROM event_runs WHERE id = :r"), {"r": run})


async def test_downgrade_is_blocked_by_event_data_and_empty_one_reapplies(
    progression: Service, database_url: str
) -> None:
    ev = progression.ev
    await progression.publish_config()
    await progression.publish_event(KEY, ev.body(ev.find("leaf", 1), ev.find("fin", 2, final=True)))
    assert DEFINITION.migrations_path is not None
    config = alembic_config(DEFINITION.migrations_path, database_url)
    with pytest.raises(RuntimeError, match="0008"):
        await asyncio.to_thread(command.downgrade, config, "0007")

    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM event_runs"))
            await conn.execute(text("DELETE FROM events"))
        await asyncio.to_thread(command.downgrade, config, "0007")
        async with engine.connect() as conn:
            tables = {
                r[0]
                for r in await conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            }
            columns = {
                r[0]
                for r in await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'items'"
                    )
                )
            }
        assert not tables & {"events", "event_runs", "event_find_progress", "event_completions"}
        assert "event_key" not in columns
        await asyncio.to_thread(command.upgrade, config, "head")
    finally:
        await engine.dispose()


async def test_event_item_constraints_in_schema(progression: Service) -> None:
    await progression.publish_config()
    assert progression.infra.sessions is not None
    base = (
        "INSERT INTO items (id, key, slot, rarity, title, media_kind, active, created_at, "
        "updated_at, {extra}) VALUES (:id, :key, 'frame', 'rare', 't', 'static', false, now(), "
        "now(), {values})"
    )
    for extra, values in (
        ("event_key, price", "'e', 5"),  # предмет события с ценой
        ("event_key, level_required", "'e', 2"),  # предмет события с уровнем
    ):
        with pytest.raises(IntegrityError):
            async with progression.infra.sessions() as session, session.begin():
                await session.execute(
                    text(base.format(extra=extra, values=values)), {"id": uuid7(), "key": "x"}
                )
    async with progression.infra.sessions() as session, session.begin():
        await session.execute(
            text(base.format(extra="event_key", values="'e'")), {"id": uuid7(), "key": "ok"}
        )
    with pytest.raises(IntegrityError):  # ни цены, ни уровня, ни события
        async with progression.infra.sessions() as session, session.begin():
            await session.execute(
                text(base.format(extra="level_required", values="NULL")),
                {"id": uuid7(), "key": "bad"},
            )


async def test_event_pages_do_not_leak_other_players_progress(progression: Service) -> None:
    ev = progression.ev
    await progression.publish_config()
    await progression.publish_event(KEY, ev.body(ev.find("leaf", 1), ev.find("fin", 2, final=True)))
    first, second = uuid7(), uuid7()
    await progression.claim(first, KEY, "leaf")
    other = (await progression.get(f"/v1/events/{KEY}", second)).json()
    assert other["found"] == 0
    assert all(f["state"] != "found" for f in other["finds"])
    assert_problem(await progression.client.get(f"/v1/events/{KEY}"), 401, "auth.unauthenticated")
