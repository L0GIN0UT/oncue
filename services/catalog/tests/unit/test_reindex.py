"""Пересборка поискового индекса: команда ``RebuildIndex`` на фейковом мигрататоре."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest
from catalog_fakes import CatalogStore, FakeCache, FakeCatalogUow
from catalog_service.application.commands.imports import UpsertImportedItem
from catalog_service.application.commands.reindex import RebuildIndex
from catalog_service.application.dto import ImportedItem, SearchDocument
from catalog_service.domain.model import ExternalSource, MediaKind
from catalog_service.domain.normalization import TitleNormalizer

from gv_core.errors import DependencyUnavailableError, InvalidInputError
from gv_testkit import FakeClock

pytestmark = pytest.mark.unit


@dataclass
class FakeMigrator:
    """Целевой индекс в памяти с внешней версией, как у OpenSearch."""

    live: tuple[str, ...] = ("catalog_media_v1",)
    target: str = "catalog_media_v2"
    docs: dict[uuid.UUID, int] = field(default_factory=dict)
    prepared: int = 0
    dropped: int = 0
    switches: int = 0
    lose: int = 0  # сколько документов «потерять» при подсчёте (проверка полноты)

    async def live_indices(self) -> tuple[str, ...]:
        return self.live

    async def prepare(self) -> None:
        self.prepared += 1

    async def drop_target(self) -> None:
        self.dropped += 1
        self.docs.clear()

    async def write(self, docs: Sequence[SearchDocument]) -> int:
        written = 0
        for doc in docs:
            if self.docs.get(doc.id, 0) >= doc.version:
                continue
            self.docs[doc.id] = doc.version
            written += 1
        return written

    async def count(self) -> int:
        return len(self.docs) - self.lose

    async def switch(self) -> tuple[str, ...]:
        previous, self.live = self.live, (self.target,)
        self.switches += 1
        return previous

    async def rollback(self) -> tuple[str, str]:  # не используется в этих тестах
        raise NotImplementedError


def _film(n: int) -> ImportedItem:
    return ImportedItem(
        kind=MediaKind.FILM, title=f"Фильм {n}", external_refs={ExternalSource.TMDB: str(n)}
    )


async def _seed(store: CatalogStore, count: int) -> None:
    upsert = UpsertImportedItem(
        lambda: FakeCatalogUow(store),
        TitleNormalizer(),
        FakeClock(),
    )
    for n in range(count):
        await upsert(_film(n))


async def test_rebuild_writes_all_switches_and_drops_cache() -> None:
    store, cache, migrator = CatalogStore(), FakeCache(), FakeMigrator()
    await _seed(store, 7)
    report = await RebuildIndex(lambda: FakeCatalogUow(store), migrator, cache, batch=3)()
    assert report.seen == 7
    assert report.written == 7  # проходы-повторы ничего не добавляют (внешняя версия)
    assert report.switched
    assert report.previous == ("catalog_media_v1",)
    assert migrator.live == ("catalog_media_v2",)
    assert cache.invalidations == 1


async def test_rebuild_is_idempotent_and_resumable() -> None:
    store, migrator = CatalogStore(), FakeMigrator()
    await _seed(store, 4)
    rebuild = RebuildIndex(lambda: FakeCatalogUow(store), migrator, None, batch=2)
    await rebuild(switch=False)
    again = await rebuild()
    assert again.written == 0  # всё уже в целевом индексе
    assert again.switched
    third = await rebuild()
    assert third.already_live
    assert not third.switched
    assert migrator.switches == 1


async def test_rebuild_refuses_to_switch_when_index_incomplete() -> None:
    store, migrator = CatalogStore(), FakeMigrator(lose=1)
    await _seed(store, 3)
    with pytest.raises(DependencyUnavailableError) as exc:
        await RebuildIndex(lambda: FakeCatalogUow(store), migrator, None)()
    assert exc.value.code == "catalog.reindex_incomplete"
    assert migrator.live == ("catalog_media_v1",)
    assert migrator.switches == 0


async def test_rebuild_no_switch_leaves_alias() -> None:
    store, cache, migrator = CatalogStore(), FakeCache(), FakeMigrator()
    await _seed(store, 2)
    report = await RebuildIndex(lambda: FakeCatalogUow(store), migrator, cache)(switch=False)
    assert not report.switched
    assert migrator.live == ("catalog_media_v1",)
    assert cache.invalidations == 0


async def test_rebuild_picks_up_changes_made_after_first_pass() -> None:
    """Единица, обновлённая во время пересборки (новая версия), попадает в целевой индекс."""
    store = CatalogStore()
    await _seed(store, 2)

    class Hooked(FakeMigrator):
        bumped = False

        async def write(self, docs: Sequence[SearchDocument]) -> int:
            result = await super().write(docs)
            if not self.bumped:
                self.bumped = True
                item = next(iter(store.items.values()))
                item.republish()  # событие пришло уже после первой порции
            return result

    hooked = Hooked()
    report = await RebuildIndex(lambda: FakeCatalogUow(store), hooked, None, batch=10)()
    item = next(iter(store.items.values()))
    assert hooked.docs[item.id] == item.version
    assert report.seen == 2


async def test_failure_after_switch_does_not_fail_rebuild() -> None:
    """Алиас уже переключён: сбой догоняющего прохода логируется, отчёт остаётся успешным."""
    store = CatalogStore()
    await _seed(store, 2)

    class FailsAfterSwitch(FakeMigrator):
        async def write(self, docs: Sequence[SearchDocument]) -> int:
            if self.switches:
                raise DependencyUnavailableError("down", code="catalog.search_unavailable")
            return await super().write(docs)

    migrator = FailsAfterSwitch()
    report = await RebuildIndex(lambda: FakeCatalogUow(store), migrator, None)()
    assert report.switched
    assert migrator.live == ("catalog_media_v2",)


async def test_sync_catches_up_without_switching() -> None:
    """После отката прежний индекс догоняется из БД, алиас не трогается."""
    store, cache, migrator = CatalogStore(), FakeCache(), FakeMigrator()
    await _seed(store, 3)
    assert await RebuildIndex(lambda: FakeCatalogUow(store), migrator, cache).sync() == 3
    assert migrator.switches == 0
    assert cache.invalidations == 1


async def test_any_error_after_switch_does_not_fail_rebuild() -> None:
    """Не только DependencyUnavailableError: сбой БД или Redis после переключения не фатален."""
    store = CatalogStore()
    await _seed(store, 2)

    class BrokenCache(FakeCache):
        async def invalidate(self) -> None:
            raise RuntimeError("redis упал")

    migrator = FakeMigrator()
    report = await RebuildIndex(lambda: FakeCatalogUow(store), migrator, BrokenCache())()
    assert report.switched
    assert migrator.live == ("catalog_media_v2",)


async def test_recreate_drops_target_and_rebuilds_from_scratch() -> None:
    """Содержимое исправили без смены версии: --recreate собирает заново, а не пропускает."""
    store, migrator = CatalogStore(), FakeMigrator()
    await _seed(store, 3)
    rebuild = RebuildIndex(lambda: FakeCatalogUow(store), migrator, None)
    await rebuild(switch=False)
    assert (await rebuild(switch=False)).written == 0  # без recreate всё пропущено
    report = await rebuild(switch=False, recreate=True)
    assert migrator.dropped == 1
    assert report.written == 3


async def test_recreate_refuses_live_target() -> None:
    store = CatalogStore()
    migrator = FakeMigrator(live=("catalog_media_v2",))
    with pytest.raises(InvalidInputError) as exc:
        await RebuildIndex(lambda: FakeCatalogUow(store), migrator, None)(recreate=True)
    assert exc.value.code == "catalog.reindex_target_live"
    assert migrator.dropped == 0


async def test_sync_fails_when_index_stays_incomplete() -> None:
    """Откат: если прежний индекс после догонки неполон, это ошибка, а не тихий успех."""
    store, cache = CatalogStore(), FakeCache()
    await _seed(store, 3)
    with pytest.raises(DependencyUnavailableError) as exc:
        await RebuildIndex(lambda: FakeCatalogUow(store), FakeMigrator(lose=1), cache).sync()
    assert exc.value.code == "catalog.reindex_incomplete"
    assert cache.invalidations == 0
