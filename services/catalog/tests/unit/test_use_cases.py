"""Use-cases catalog на фейках портов (без IO)."""

from __future__ import annotations

import uuid

import pytest
from catalog_fakes import (
    CatalogStore,
    FakeCache,
    FakeCatalogUow,
    FakeFileReader,
    FakeFilmProvider,
    FakeMusicProvider,
    FakeSearch,
)
from catalog_service.application.commands.admin import (
    AdminContext,
    BlockItem,
    ReplaceAliases,
    UnblockItem,
)
from catalog_service.application.commands.imports import (
    IMPORT_TASK,
    ImportCommand,
    RequestImport,
    RunImport,
    UpsertImportedItem,
)
from catalog_service.application.commands.projections import (
    ClueAssetsEvent,
    IndexItem,
    MarkClueAssets,
)
from catalog_service.application.dto import ImportedItem, PersonRef, SearchDocument
from catalog_service.application.queries.search import (
    Autocomplete,
    GetImport,
    GetItem,
    ListItems,
    Search,
    SearchLimits,
)
from catalog_service.domain.events import MediaItemBlocked, MediaItemCreated, MediaItemUpdated
from catalog_service.domain.import_job import ImportSource, ImportStatus
from catalog_service.domain.model import (
    AliasDraft,
    AliasKind,
    AliasSource,
    ExternalSource,
    MediaDomain,
    MediaKind,
    MediaMetrics,
    PersonRole,
)
from catalog_service.domain.normalization import TitleNormalizer

from gv_core.errors import (
    ConflictError,
    DependencyUnavailableError,
    FeatureDisabledError,
    InvalidInputError,
    NotFoundError,
)
from gv_core.ids import uuid7
from gv_testkit import FakeClock, InMemoryTaskQueue

pytestmark = pytest.mark.unit

N = TitleNormalizer()
ALL_SOURCES = frozenset(ImportSource)


def film(tmdb: str = "157336", title: str = "Интерстеллар", **kw: object) -> ImportedItem:
    base: dict[str, object] = {
        "kind": MediaKind.FILM,
        "title": title,
        "original_title": "Interstellar",
        "year": 2014,
        "persons": (PersonRef("tmdb:525", "Кристофер Нолан", PersonRole.DIRECTOR),),
        "external_refs": {ExternalSource.TMDB: tmdb},
        "metrics": MediaMetrics(popularity=100.0),
    }
    base.update(kw)
    return ImportedItem(**base)  # type: ignore[arg-type]


def track(ref: str, title: str) -> ImportedItem:
    return ImportedItem(
        kind=MediaKind.TRACK,
        title=title,
        persons=(PersonRef("name:queen", "Queen", PersonRole.ARTIST),),
        external_refs={ExternalSource.SPOTIFY: ref},
    )


class Env:
    def __init__(self) -> None:
        self.store = CatalogStore()
        self.cache = FakeCache()
        self.tasks = InMemoryTaskQueue()
        self.clock = FakeClock()
        self.upsert = UpsertImportedItem(self.uow, N, self.clock)

    def uow(self) -> FakeCatalogUow:
        return FakeCatalogUow(self.store)

    def ctx(self, key: str = "key-00000001", actor: uuid.UUID | None = None) -> AdminContext:
        return AdminContext(actor or ACTOR, key, "проверка модератором")

    async def create_film(self, **kw: object) -> uuid.UUID:
        await self.upsert(film(**kw))  # type: ignore[arg-type]
        return next(reversed(self.store.items))


ACTOR = uuid7()


@pytest.fixture
def env() -> Env:
    return Env()


# ------------------------------------------------------------------ импорт единиц


async def test_upsert_creates_then_unchanged_then_updated(env: Env) -> None:
    assert (await env.upsert(film())).value == "created"
    assert (await env.upsert(film())).value == "unchanged"
    assert (await env.upsert(film(year=2015))).value == "updated"
    assert len(env.store.items) == 1
    kinds = [type(e) for e in env.store.events]
    assert kinds == [MediaItemCreated, MediaItemUpdated]


async def test_upsert_resolves_persons_stably(env: Env) -> None:
    await env.upsert(film())
    await env.upsert(film(tmdb="2", title="Начало"))
    ids = {p.person_id for i in env.store.items.values() for p in i.persons}
    assert len(ids) == 1


async def test_upsert_keeps_manual_aliases(env: Env) -> None:
    item_id = await env.create_film()
    await ReplaceAliases(env.uow, env.cache, N)(
        item_id,
        [AliasDraft("Интерстеллар", AliasKind.TITLE), AliasDraft("Интер", AliasKind.SHORT)],
        env.ctx(),
    )
    await env.upsert(film(year=2015))
    aliases = {a.normalized: a.source for a in env.store.items[item_id].aliases}
    assert aliases["интер"] is AliasSource.MANUAL


# ------------------------------------------------------------------ админ-мутации


async def test_block_is_idempotent_by_key(env: Env) -> None:
    item_id = await env.create_film()
    block = BlockItem(env.uow, env.cache)
    first = await block(item_id, "copyright.claim", env.ctx())
    again = await block(item_id, "copyright.claim", env.ctx())
    assert first == again
    assert first["status"] == "blocked"
    assert sum(isinstance(e, MediaItemBlocked) for e in env.store.events) == 1
    assert env.cache.invalidations == 1
    recorded = env.store.actions[(ACTOR, "key-00000001")]
    assert recorded.reason == "проверка модератором"
    assert recorded.action == "item.block"


async def test_same_key_other_request_is_conflict(env: Env) -> None:
    item_id = await env.create_film()
    await BlockItem(env.uow, env.cache)(item_id, "moderation", env.ctx())
    with pytest.raises(ConflictError) as exc:
        await UnblockItem(env.uow, env.cache)(item_id, env.ctx())
    assert exc.value.code == "idempotency.key_reused"


async def test_block_unknown_item_is_not_found(env: Env) -> None:
    with pytest.raises(NotFoundError):
        await BlockItem(env.uow, env.cache)(uuid7(), "moderation", env.ctx())


async def test_unblock_after_block(env: Env) -> None:
    item_id = await env.create_film()
    await BlockItem(env.uow, env.cache)(item_id, "moderation", env.ctx("k-block-1"))
    view = await UnblockItem(env.uow, env.cache)(item_id, env.ctx("k-unblock-1"))
    assert view["status"] == "active"
    assert view["version"] == 3


async def test_replace_aliases_without_change_emits_nothing(env: Env) -> None:
    item_id = await env.create_film()
    replace = ReplaceAliases(env.uow, env.cache, N)
    drafts = [AliasDraft("Интерстеллар", AliasKind.TITLE)]
    await replace(item_id, drafts, env.ctx("k-1-aaaaaa"))
    events_before = len(env.store.events)
    await replace(item_id, drafts, env.ctx("k-2-aaaaaa"))
    assert len(env.store.events) == events_before


# ------------------------------------------------------------------ задания импорта


def cmd(source: ImportSource = ImportSource.TMDB, **kw: object) -> ImportCommand:
    base: dict[str, object] = {
        "source": source,
        "ref": "157336",
        "file_id": None,
        "reason": "наполнение",
        "actor_id": ACTOR,
        "idempotency_key": "imp-0000001",
    }
    base.update(kw)
    return ImportCommand(**base)  # type: ignore[arg-type]


async def test_request_import_creates_job_and_enqueues(env: Env) -> None:
    view = await RequestImport(env.uow, env.tasks, ALL_SOURCES)(cmd())
    assert view["status"] == "pending"
    [task] = env.tasks.tasks
    assert task["task_type"] == IMPORT_TASK
    assert task["task_id"] == view["import_id"]
    assert task["payload"] == {"import_id": view["import_id"]}


async def test_request_import_is_idempotent(env: Env) -> None:
    request = RequestImport(env.uow, env.tasks, ALL_SOURCES)
    first = await request(cmd())
    second = await request(cmd())
    assert first["import_id"] == second["import_id"]
    assert len(env.store.jobs) == 1
    # Незавершённое задание переставляется — задача одна и та же (task_id).
    assert {t["task_id"] for t in env.tasks.tasks} == {first["import_id"]}
    with pytest.raises(ConflictError):
        await request(cmd(ref="1"))


async def test_request_import_disabled_source(env: Env) -> None:
    with pytest.raises(FeatureDisabledError) as exc:
        await RequestImport(env.uow, env.tasks, frozenset({ImportSource.FILE}))(cmd())
    assert exc.value.code == "catalog.source_disabled"


@pytest.mark.parametrize(
    ("source", "ref", "file_id"),
    [
        (ImportSource.FILE, None, "../etc/passwd"),
        (ImportSource.FILE, None, "films.json"),
        (ImportSource.FILE, "x", "films.jsonl"),
        (ImportSource.TMDB, None, None),
        (ImportSource.TMDB, "search:matrix", None),
        (ImportSource.SPOTIFY, "bad ref!", None),
        (ImportSource.SPOTIFY, "search:" + "x" * 101, None),
    ],
)
async def test_request_import_validates_refs(
    env: Env, source: ImportSource, ref: str | None, file_id: str | None
) -> None:
    with pytest.raises(InvalidInputError):
        await RequestImport(env.uow, env.tasks, ALL_SOURCES)(
            cmd(source=source, ref=ref, file_id=file_id)
        )


async def test_request_import_enqueue_failure_is_dependency_error(env: Env) -> None:
    class Broken:
        async def enqueue(
            self, task_type: str, payload: dict[str, object], *, task_id: str
        ) -> None:
            raise ConnectionError("rabbit down")

    with pytest.raises(DependencyUnavailableError):
        await RequestImport(env.uow, Broken(), ALL_SOURCES)(cmd())  # type: ignore[arg-type]
    assert len(env.store.jobs) == 1  # задание сохранено, повтор с тем же ключом переставит


def run_import(env: Env, **kw: object) -> RunImport:
    defaults: dict[str, object] = {
        "film_provider": FakeFilmProvider({"157336": film()}),
        "music_providers": {},
        "file_reader": FakeFileReader(),
    }
    defaults.update(kw)
    return RunImport(env.uow, env.upsert, **defaults)  # type: ignore[arg-type]


async def test_run_tmdb_import(env: Env) -> None:
    view = await RequestImport(env.uow, env.tasks, ALL_SOURCES)(cmd())
    job_id = uuid.UUID(view["import_id"])
    await run_import(env)(job_id)
    job = await GetImport(env.uow)(job_id)
    assert job["status"] == "done"
    assert job["counters"]["created"] == 1
    # Повторная доставка задачи: задание завершено — ничего не делаем.
    await run_import(env)(job_id)
    assert (await GetImport(env.uow)(job_id))["counters"]["created"] == 1


async def test_run_file_import_counts_bad_lines(env: Env) -> None:
    reader = FakeFileReader(
        {
            "films.jsonl": [
                film(tmdb="1", title="Брат"),
                ValueError("строка 2: нет title"),
                film(tmdb="2", title="Брат 2", year=1800),
            ]
        }
    )
    view = await RequestImport(env.uow, env.tasks, ALL_SOURCES)(
        cmd(source=ImportSource.FILE, ref=None, file_id="films.jsonl")
    )
    await run_import(env, file_reader=reader)(uuid.UUID(view["import_id"]))
    job = env.store.jobs[uuid.UUID(view["import_id"])]
    assert job.status is ImportStatus.DONE
    assert job.counters.as_dict() == {"created": 1, "updated": 0, "unchanged": 0, "failed": 2}


async def test_run_import_missing_film_fails_job(env: Env) -> None:
    view = await RequestImport(env.uow, env.tasks, ALL_SOURCES)(cmd(ref="999"))
    await run_import(env)(uuid.UUID(view["import_id"]))
    job = env.store.jobs[uuid.UUID(view["import_id"])]
    assert job.status is ImportStatus.FAILED
    assert job.error == "catalog.external_not_found"


async def test_run_music_search_and_get(env: Env) -> None:
    provider = FakeMusicProvider(
        {"a1": track("a1", "Bohemian Rhapsody"), "a2": track("a2", "We Will Rock You")}
    )
    request = RequestImport(env.uow, env.tasks, ALL_SOURCES)
    search_view = await request(
        cmd(source=ImportSource.SPOTIFY, ref="search:rock", idempotency_key="k-search-1")
    )
    get_view = await request(cmd(source=ImportSource.SPOTIFY, ref="a1", idempotency_key="k-get-1"))
    runner = run_import(env, music_providers={ImportSource.SPOTIFY: provider})
    await runner(uuid.UUID(search_view["import_id"]))
    await runner(uuid.UUID(get_view["import_id"]))
    assert {i.title for i in env.store.items.values()} == {"We Will Rock You", "Bohemian Rhapsody"}


async def test_run_music_unavailable_or_disabled_fails_job(env: Env) -> None:
    request = RequestImport(env.uow, env.tasks, ALL_SOURCES)
    v1 = await request(cmd(source=ImportSource.SPOTIFY, ref="a1", idempotency_key="k-down-01"))
    v2 = await request(cmd(source=ImportSource.YANDEX_MUSIC, ref="1", idempotency_key="k-off-001"))
    runner = run_import(env, music_providers={ImportSource.SPOTIFY: FakeMusicProvider(down=True)})
    await runner(uuid.UUID(v1["import_id"]))
    await runner(uuid.UUID(v2["import_id"]))
    assert env.store.jobs[uuid.UUID(v1["import_id"])].error == "catalog.source_unavailable"
    assert env.store.jobs[uuid.UUID(v2["import_id"])].error == "catalog.source_disabled"


async def test_run_unknown_job_is_noop(env: Env) -> None:
    await run_import(env)(uuid7())


# ------------------------------------------------------------------ поиск


def autocomplete(env: Env, primary: FakeSearch | None, fallback: FakeSearch) -> Autocomplete:
    return Autocomplete(primary, fallback, env.cache, N, SearchLimits())


async def indexed(env: Env, search: FakeSearch) -> uuid.UUID:
    item_id = await env.create_film()
    await search.index(SearchDocument.from_item(env.store.items[item_id]))
    return item_id


async def test_autocomplete_uses_primary_and_caches(env: Env) -> None:
    primary, fallback = FakeSearch(), FakeSearch()
    item_id = await indexed(env, primary)
    ac = autocomplete(env, primary, fallback)
    [hit] = await ac("Инте", None, 5)
    assert hit.id == item_id
    await ac("инте", None, 5)  # тот же нормализованный ключ — из кэша
    assert primary.calls == 1
    assert fallback.calls == 0


async def test_autocomplete_falls_back_when_primary_down(env: Env) -> None:
    primary, fallback = FakeSearch(down=True), FakeSearch()
    item_id = await indexed(env, fallback)
    [hit] = await autocomplete(env, primary, fallback)("interst", MediaDomain.FILM, 5)
    assert hit.id == item_id


async def test_autocomplete_without_primary(env: Env) -> None:
    fallback = FakeSearch()
    await indexed(env, fallback)
    assert len(await autocomplete(env, None, fallback)("ин", None, 5)) == 1


async def test_autocomplete_short_and_long_queries(env: Env) -> None:
    ac = autocomplete(env, None, FakeSearch())
    assert await ac("и", None, 5) == []
    assert await ac("!!!", None, 5) == []
    with pytest.raises(InvalidInputError) as exc:
        await ac("x" * 65, None, 5)
    assert exc.value.code == "catalog.query_too_long"


async def test_autocomplete_clamps_limit(env: Env) -> None:
    fallback = FakeSearch()
    for i in range(15):
        await env.upsert(film(tmdb=str(i), title=f"Брат {i}"))
    for item in env.store.items.values():
        await fallback.index(SearchDocument.from_item(item))
    assert len(await autocomplete(env, None, fallback)("брат", None, 100)) == 10


async def test_search_falls_back(env: Env) -> None:
    primary, fallback = FakeSearch(down=True), FakeSearch()
    await indexed(env, fallback)
    hits = await Search(primary, fallback, N, SearchLimits())("стелл", None, 5)
    assert len(hits) == 1


async def test_blocked_item_is_hidden_publicly_but_visible_to_admin(env: Env) -> None:
    item_id = await env.create_film()
    await BlockItem(env.uow, env.cache)(item_id, "moderation", env.ctx())
    with pytest.raises(NotFoundError):
        await GetItem(env.uow).public(item_id)
    assert (await GetItem(env.uow).admin(item_id))["status"] == "blocked"


async def test_list_items_filters(env: Env) -> None:
    await env.create_film()
    await env.upsert(track("t1", "Bohemian Rhapsody"))
    page = await ListItems(env.uow, N)("bohem", None, None, None, 20)
    assert [i.title for i in page.items] == ["Bohemian Rhapsody"]
    page = await ListItems(env.uow, N)(None, MediaDomain.FILM, "active", None, 20)
    assert [i.title for i in page.items] == ["Интерстеллар"]


async def test_get_import_unknown(env: Env) -> None:
    with pytest.raises(NotFoundError):
        await GetImport(env.uow)(uuid7())


# ------------------------------------------------------------------ проекции


async def test_index_item_respects_versions_and_invalidates_cache(env: Env) -> None:
    index = FakeSearch()
    item_id = await env.create_film()
    items = env.uow().items
    use_case = IndexItem(items, index, env.cache)
    assert await use_case(item_id, 1)
    assert not await use_case(item_id, 1)  # повтор — индекс отбрасывает
    assert env.cache.invalidations == 1
    await BlockItem(env.uow, env.cache)(item_id, "moderation", env.ctx())
    assert await use_case(item_id, 2)
    assert index.docs[item_id].status == "blocked"


async def test_index_item_disabled_or_missing(env: Env) -> None:
    items = env.uow().items
    assert not await IndexItem(items, None, None)(uuid7(), 1)
    assert not await IndexItem(items, FakeSearch(), None)(uuid7(), 1)


async def test_index_item_db_behind_event_is_retryable_error(env: Env) -> None:
    item_id = await env.create_film()
    with pytest.raises(RuntimeError):
        await IndexItem(env.uow().items, FakeSearch(), None)(item_id, 5)


def clue_event(item_id: uuid.UUID, version: int, **kw: object) -> ClueAssetsEvent:
    base: dict[str, object] = {
        "media_item_id": item_id,
        "asset_set_id": uuid.UUID(int=1),
        "clue_type": "poster_masked",
        "profile_key": "default",
        "status": "ready",
        "version": version,
        "levels_count": 5,
    }
    base.update(kw)
    return ClueAssetsEvent(**base)  # type: ignore[arg-type]


async def test_mark_clue_assets_drops_stale_versions(env: Env) -> None:
    item_id = await env.create_film()
    uow = env.uow()
    mark = MarkClueAssets(uow.items, uow.clue_status)
    assert await mark(clue_event(item_id, 2))
    assert not await mark(clue_event(item_id, 1, status="failed"))
    assert not await mark(clue_event(item_id, 2))
    assert env.store.clue[(item_id, "poster_masked", "default")].status == "ready"
    # Новый набор ассетов (другой id) заменяет старый независимо от версии.
    assert await mark(clue_event(item_id, 1, asset_set_id=uuid.UUID(int=2), status="failed"))


async def test_mark_clue_assets_unknown_item(env: Env) -> None:
    uow = env.uow()
    assert not await MarkClueAssets(uow.items, uow.clue_status)(clue_event(uuid7(), 1))
