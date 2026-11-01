"""Догрузка авторов сборников в проекцию: дописывает только отсутствующих, повтор безопасен."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from progression_service.application.admin.collection_authors import (
    BackfillCollectionAuthors,
    CollectionCard,
)

from gv_core.ids import uuid7
from gv_messaging import Outcome

pytestmark = pytest.mark.component

Service = Any  # фикстура `progression` описана в conftest.py


class FakeDirectory:
    def __init__(self, cards: list[CollectionCard]) -> None:
        self._cards = cards

    async def cards(self) -> AsyncIterator[CollectionCard]:
        for card in self._cards:
            yield card


async def test_backfill_adds_missing_and_keeps_existing(progression: Service) -> None:
    await progression.publish_config()
    owner, other = uuid7(), uuid7()
    known, old, curated = uuid7(), uuid7(), uuid7()
    event = progression.collection_published(owner, collection_id=known)
    assert await progression.deliver(event) is Outcome.PROCESSED
    directory = FakeDirectory(
        [
            CollectionCard(known, "ugc", other),  # уже есть: событие точнее, не перезаписываем
            CollectionCard(old, "ugc", owner),
            CollectionCard(curated, "curated", None),
        ]
    )

    def run() -> BackfillCollectionAuthors:
        return BackfillCollectionAuthors(
            progression.container.uow_factory(), directory, progression.clock
        )

    report = await run()()
    assert (report.seen, report.written, report.skipped) == (3, 2, 1)
    again = await run()()
    assert (again.written, again.skipped) == (0, 3)

    rows = {
        r.collection_id: (r.kind, r.author_id) for r in await progression.rows("collection_authors")
    }
    assert rows == {known: ("ugc", owner), old: ("ugc", owner), curated: ("curated", None)}


async def test_backfilled_owner_gets_no_reward_for_own_collection(progression: Service) -> None:
    await progression.publish_config()
    owner = uuid7()
    collection = uuid7()
    await BackfillCollectionAuthors(
        progression.container.uow_factory(),
        FakeDirectory([CollectionCard(collection, "ugc", owner)]),
        progression.clock,
    )()

    event = progression.game_finished(owner, [100, 100, 100], collection_id=collection)
    assert await progression.deliver(event) is Outcome.PROCESSED

    assert await progression.xp_total(owner) == 0
    assert UUID(str(collection))
