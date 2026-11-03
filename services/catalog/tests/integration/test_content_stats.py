"""Статистика угадываний на реальном Postgres: события → счётчики → отчёт (ADR 0076).

События — примеры из ``contracts/events`` с подставленными ``media_item_id``: так тест
следит и за соответствием разбора контрактам игры и матча.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from catalog_service.application.commands.imports import UpsertImportedItem
from catalog_service.application.dto import ImportedItem, PersonRef
from catalog_service.domain.model import ExternalSource, MediaKind, PersonRole
from catalog_service.infrastructure.container import CatalogContainer
from catalog_service.infrastructure.db.tables import content_mode_buckets, content_mode_stats
from catalog_service.infrastructure.messaging import CONSUMED_TOPICS, build_event_handlers
from sqlalchemy import select

from gv_core.errors import InvalidInputError
from gv_messaging import EventEnvelope, EventProcessor, Inbox, Outcome
from gv_service import Infrastructure
from gv_testkit import FakeClock, FakeDeadLetterSink, load_example

pytestmark = pytest.mark.integration

MODE = "music.duel.classic"


def _processor(infra: Infrastructure, dead: FakeDeadLetterSink | None = None) -> EventProcessor:
    assert infra.sessions is not None and infra.tables is not None
    return EventProcessor(
        group="catalog.consumer",
        registry=build_event_handlers(infra),
        session_factory=infra.sessions,
        inbox=Inbox(infra.tables.inbox),
        dead_letters=dead or FakeDeadLetterSink(),
        clock=FakeClock(),
    )


async def _track(c: CatalogContainer, ref: str) -> uuid.UUID:
    item = ImportedItem(
        kind=MediaKind.TRACK,
        title=f"Track {ref}",
        persons=(PersonRef(f"name:{ref}", "Artist", PersonRole.ARTIST),),
        external_refs={ExternalSource.SPOTIFY: ref},
    )
    await UpsertImportedItem(c.uow, c.normalizer, c.infra.clock)(item)
    async with c.uow() as uow:
        found = await uow.items.find_by_external_ref(ExternalSource.SPOTIFY, ref)
    assert found is not None
    return found.id


def _match(
    item: uuid.UUID, results: list[dict[str, Any]], *, event_id: bool = True
) -> EventEnvelope:
    envelope = load_example("match.MatchFinished", fresh_id=event_id)
    players = [
        {**p, "user_id": r["user_id"], "finish": "completed"}
        for p, r in zip(envelope.payload["players"], results, strict=True)
    ]
    rounds = [{"index": 0, "media_item_id": str(item), "results": results}]
    payload = {**envelope.payload, "mode_key": MODE, "players": players, "rounds": rounds}
    return envelope.model_copy(update={"payload": payload})


def _game(item: uuid.UUID, outcome: str, level: int | None, elapsed: int | None) -> EventEnvelope:
    envelope = load_example("game.GameFinished")
    rnd = {
        "index": 0,
        "media_item_id": str(item),
        "outcome": outcome,
        "level": level,
        "points": 0,
        "elapsed_ms": elapsed,
    }
    return envelope.model_copy(
        update={"payload": {**envelope.payload, "mode_key": MODE, "rounds": [rnd]}}
    )


def _result(user: int, level: int | None, elapsed: int | None) -> dict[str, Any]:
    return {
        "user_id": str(uuid.UUID(int=user)),
        "level": level,
        "partial": False,
        "score": 0,
        "elapsed_ms": elapsed,
    }


async def _counters(c: CatalogContainer, item: uuid.UUID) -> dict[str, Any]:
    assert c.infra.sessions is not None
    async with c.infra.sessions() as s:
        row = (
            (
                await s.execute(
                    select(content_mode_stats).where(content_mode_stats.c.media_item_id == item)
                )
            )
            .mappings()
            .one()
        )
        buckets = (
            await s.execute(
                select(
                    content_mode_buckets.c.kind,
                    content_mode_buckets.c.bucket,
                    content_mode_buckets.c.n,
                ).where(content_mode_buckets.c.media_item_id == item)
            )
        ).all()
    return dict(row) | {"buckets": {(b.kind, b.bucket): b.n for b in buckets}}


def test_catalog_consumes_game_and_match_topics() -> None:
    assert {"game.session.v1", "match.match.v1"} <= set(CONSUMED_TOPICS)


async def test_redelivery_does_not_count_twice(
    catalog_infra: Infrastructure, container: CatalogContainer
) -> None:
    item = await _track(container, "t1")
    envelope = _match(item, [_result(2, 1, 4210), _result(3, None, None)])
    processor = _processor(catalog_infra)

    assert await processor.process(envelope.to_bytes(), source="t") is Outcome.PROCESSED
    assert await processor.process(envelope.to_bytes(), source="t") is Outcome.DUPLICATE

    row = await _counters(container, item)
    assert (row["shown"], row["exact"], row["miss"]) == (2, 1, 1)
    assert (row["time_sum_ms"], row["time_count"]) == (4210, 1)
    assert row["buckets"] == {("level", 1): 1, ("time", 4): 1}


async def test_solo_and_online_results_add_up_per_item_and_mode(
    catalog_infra: Infrastructure, container: CatalogContainer
) -> None:
    item = await _track(container, "t2")
    processor = _processor(catalog_infra)
    for envelope in (
        _game(item, "exact", 2, 7000),
        _game(item, "gave_up", None, None),
        _match(item, [_result(2, 2, 6500), _result(3, 1, 500)]),
    ):
        assert await processor.process(envelope.to_bytes(), source="t") is Outcome.PROCESSED

    row = await _counters(container, item)
    assert (row["shown"], row["exact"], row["gave_up"]) == (4, 3, 1)
    assert row["buckets"][("level", 2)] == 2
    assert row["buckets"][("level", 1)] == 1


async def test_concurrent_events_for_the_same_item_do_not_lose_increments(
    catalog_infra: Infrastructure, container: CatalogContainer
) -> None:
    item = await _track(container, "t3")
    processor = _processor(catalog_infra)
    events = [_game(item, "exact", 1, 1000 + n) for n in range(8)]
    outcomes = await asyncio.gather(*(processor.process(e.to_bytes(), source="t") for e in events))
    assert set(outcomes) == {Outcome.PROCESSED}
    row = await _counters(container, item)
    assert (row["shown"], row["exact"]) == (8, 8)
    assert row["buckets"][("level", 1)] == 8


async def test_malformed_payload_goes_to_dead_letters_without_counting(
    catalog_infra: Infrastructure, container: CatalogContainer
) -> None:
    dead = FakeDeadLetterSink()
    envelope = load_example("game.GameFinished")
    broken = envelope.model_copy(update={"payload": {**envelope.payload, "rounds": "oops"}})
    assert (
        await _processor(catalog_infra, dead).process(broken.to_bytes(), source="t") is Outcome.DEAD
    )
    assert len(dead.letters) == 1


async def test_report_orders_by_exact_rate_and_respects_min_shown(
    catalog_infra: Infrastructure, container: CatalogContainer
) -> None:
    easy = await _track(container, "easy")
    hard = await _track(container, "hard")
    rare = await _track(container, "rare")
    processor = _processor(catalog_infra)
    plan = [(easy, "exact", 3000)] * 9 + [(easy, "miss", None)]
    plan += [(hard, "exact", 9000)] * 2 + [(hard, "miss", None)] * 8
    plan += [(rare, "exact", 1000)]  # 100%, но показов мало
    for item, outcome, elapsed in plan:
        level = 1 if outcome == "exact" else None
        envelope = _game(item, outcome, level, elapsed)
        await processor.process(envelope.to_bytes(), source="t")

    report = await container.easiest_content(min_shown=10)
    assert [r.title for r in report] == ["Track easy", "Track hard"]
    top = report[0]
    assert (top.shown, top.exact, top.exact_rate) == (10, 9, 0.9)
    assert top.avg_time_ms == 3000
    assert top.median_time_ms == 3500  # корзина 3 с (ширина 1 с), середина
    assert top.exact_by_level == {1: 9}
    assert [r.title for r in await container.easiest_content(min_shown=1, limit=1)] == [
        "Track rare"
    ]
    assert await container.easiest_content(min_shown=10, mode_key="film.duel.frame") == []
    with pytest.raises(InvalidInputError):
        await container.easiest_content(limit=10_000)
