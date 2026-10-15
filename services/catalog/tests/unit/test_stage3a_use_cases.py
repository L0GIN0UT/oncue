"""Use-cases этапа 3a на фейках: тексты загадок, импорт текстов, переопубликация (M7–M9)."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

import pytest
from catalog_fakes import CatalogStore, FakeCatalogUow
from catalog_service.application.commands.admin import (
    AdminContext,
    ApproveClueTexts,
    ClueTextsEdit,
    EditClueTexts,
    GetClueTexts,
)
from catalog_service.application.commands.imports import UpsertImportedItem
from catalog_service.application.commands.republish import (
    REPUBLISH_TASK,
    RequestRepublish,
    RunRepublishBatch,
)
from catalog_service.application.dto import ImportedClueTexts, ImportedItem, PersonRef
from catalog_service.domain.clue_texts import ClueTextStatus
from catalog_service.domain.events import MediaItemCreated, MediaItemUpdated
from catalog_service.domain.model import (
    ExternalSource,
    MediaDomain,
    MediaKind,
    PersonRole,
    SourceMediaDraft,
    SourceMediaKind,
    SourceMediaOrigin,
)
from catalog_service.domain.normalization import TitleNormalizer
from catalog_service.domain.republish import RepublishStatus

from gv_core.errors import ConflictError, DomainError, NotFoundError
from gv_core.ids import uuid7
from gv_testkit import FakeClock, InMemoryTaskQueue

pytestmark = pytest.mark.unit

N = TitleNormalizer()
ACTOR = uuid7()
PLOT = "Кристофер Нолан снял «Интерстеллар»: Купер летит сквозь червоточину."


def film(tmdb: str = "157336", **texts: Any) -> ImportedItem:
    return ImportedItem(
        kind=MediaKind.FILM,
        title="Интерстеллар",
        original_title="Interstellar",
        year=2014,
        persons=(PersonRef("tmdb:525", "Кристофер Нолан", PersonRole.DIRECTOR),),
        external_refs={ExternalSource.TMDB: tmdb},
        countries=("US", "GB"),
        source_media=tuple(
            SourceMediaDraft(
                SourceMediaKind.FRAME,
                SourceMediaOrigin.TMDB,
                f"https://image.tmdb.org/t/p/original/f{i}.jpg",
                position=i,
                source_url="https://www.themoviedb.org/movie/157336",
                license_note="TMDB",
            )
            for i in (1, 2)
        ),
        clue_texts=ImportedClueTexts(plot_raw=PLOT, tagline="Шаг") if not texts else None,
        **texts,
    )


class Env:
    def __init__(self) -> None:
        self.store = CatalogStore()
        self.tasks = InMemoryTaskQueue()
        self.upsert = UpsertImportedItem(self.uow, N, FakeClock())
        self.get = GetClueTexts(self.uow)
        self.edit = EditClueTexts(self.uow, None)
        self.approve = ApproveClueTexts(self.uow, None)
        self.republish = RequestRepublish(self.uow, self.tasks)
        self.run_batch = RunRepublishBatch(self.uow, self.tasks, batch=2)

    def uow(self) -> FakeCatalogUow:
        return FakeCatalogUow(self.store)

    def ctx(self, key: str | None = None) -> AdminContext:
        return AdminContext(ACTOR, key or f"k-{uuid7().hex}", "проверка модератором")

    def snapshots(self) -> list[dict[str, Any]]:
        return [
            e.payload()
            for e in self.store.events
            if isinstance(e, MediaItemCreated | MediaItemUpdated)
        ]


@pytest.fixture
def env() -> Env:
    return Env()


async def test_tmdb_texts_become_masked_draft_not_published(env: Env) -> None:
    assert (await env.upsert(film())).value == "created"
    (item_id,) = env.store.items
    view = await env.get(item_id)
    assert view["plot_raw"] == PLOT
    assert view["plot_masked"] == "… снял «…»: Купер летит сквозь червоточину."
    assert (view["status"], view["revision"], view["tagline"]) == ("draft", 1, "Шаг")
    (created,) = env.snapshots()
    assert "clue_texts" not in created  # черновик наружу не уходит
    assert created["countries"] == ["US", "GB"]
    frames = [s for s in created["source_media"] if s["kind"] == "frame"]
    assert [(f["position"], f["license_note"]) for f in frames] == [(1, "TMDB"), (2, "TMDB")]
    # повторный импорт того же — без изменений
    assert (await env.upsert(film())).value == "unchanged"


async def test_approve_publishes_and_edit_after_approval_keeps_published(env: Env) -> None:
    await env.upsert(film())
    (item_id,) = env.store.items
    approved = await env.approve(item_id, 1, env.ctx())
    assert (approved["status"], approved["approved_revision"]) == ("approved", 1)
    updated = env.snapshots()[-1]
    assert updated["clue_texts"] == {
        "plot_masked": "… снял «…»: Купер летит сквозь червоточину.",
        "tagline": "Шаг",
    }
    assert updated["changed_fields"] == ["clue_texts"]
    events_before = len(env.store.events)

    edit = ClueTextsEdit("Новый сюжет …", "Шаг", "🚀🌌", base_revision=1)
    edited = await env.edit(item_id, edit, env.ctx())
    assert (edited["status"], edited["revision"]) == ("draft", 2)
    assert len(env.store.events) == events_before  # правка не публикуется
    stored = env.store.items[item_id]
    assert stored.clue_texts.plot_masked == "… снял «…»: Купер летит сквозь червоточину."

    with pytest.raises(ConflictError) as exc:
        await env.approve(item_id, 1, env.ctx())
    assert exc.value.code == "catalog.clue_texts_revision_mismatch"
    await env.approve(item_id, 2, env.ctx())
    assert env.snapshots()[-1]["clue_texts"]["emoji"] == "🚀🌌"
    assert env.store.texts[item_id].status is ClueTextStatus.APPROVED


async def test_clue_text_mutations_are_idempotent_and_audited(env: Env) -> None:
    await env.upsert(film())
    (item_id,) = env.store.items
    ctx = env.ctx("k-approve-0001")
    first = await env.approve(item_id, 1, ctx)
    again = await env.approve(item_id, 1, ctx)
    assert first == again
    assert sum(isinstance(e, MediaItemUpdated) for e in env.store.events) == 1
    assert env.store.actions[(ACTOR, "k-approve-0001")].action == "item.clue_texts_approve"
    with pytest.raises(ConflictError) as exc:
        await env.edit(item_id, ClueTextsEdit("x", None, None, 1), ctx)
    assert exc.value.code == "idempotency.key_reused"


async def test_edit_validation_and_missing_item(env: Env) -> None:
    await env.upsert(film())
    (item_id,) = env.store.items
    with pytest.raises(DomainError):
        await env.edit(item_id, ClueTextsEdit(None, None, "🚀" * 13, 1), env.ctx())
    with pytest.raises(NotFoundError):
        await env.get(uuid7())
    with pytest.raises(NotFoundError):
        await env.edit(uuid7(), ClueTextsEdit("x", None, None, 0), env.ctx())
    with pytest.raises(DomainError) as empty:
        await env.edit(item_id, ClueTextsEdit(None, None, None, 1), env.ctx())
        await env.approve(item_id, 2, env.ctx())
    assert empty.value.code == "catalog.clue_texts_empty"


async def test_seed_texts_are_approved_and_published_in_created(env: Env) -> None:
    seed = dataclasses.replace(
        film(tmdb="1"),
        clue_texts=ImportedClueTexts(plot_masked="Сюжет …", tagline="Слоган", emoji="🚀🌌⏳"),
        texts_authoritative=True,
    )
    await env.upsert(seed)
    (created,) = env.snapshots()
    assert created["clue_texts"] == {
        "plot_masked": "Сюжет …",
        "tagline": "Слоган",
        "emoji": "🚀🌌⏳",
    }
    assert created["version"] == 1
    (texts,) = env.store.texts.values()
    assert (texts.status, texts.approved_revision) == (ClueTextStatus.APPROVED, 1)


async def test_moderator_edit_survives_reimport(env: Env) -> None:
    await env.upsert(film())
    (item_id,) = env.store.items
    await env.edit(item_id, ClueTextsEdit("Рукописный …", "Шаг", None, 1), env.ctx())
    await env.upsert(film())
    assert env.store.texts[item_id].plot_masked == "Рукописный …"


async def test_republish_in_batches_is_idempotent(env: Env) -> None:
    for i in range(3):
        await env.upsert(film(tmdb=str(i)))
    await env.upsert(
        ImportedItem(
            kind=MediaKind.TRACK, title="Трек", external_refs={ExternalSource.SPOTIFY: "s"}
        )
    )
    versions = {i: item.version for i, item in env.store.items.items()}
    ctx = env.ctx("k-republish-01")
    response = await env.republish(MediaDomain.FILM, ctx)
    assert await env.republish(MediaDomain.FILM, ctx) == response  # тот же ключ — то же задание
    job_id = uuid.UUID(response["job_id"])
    (task,) = env.tasks.tasks
    assert task["task_type"] == REPUBLISH_TASK
    assert task["payload"] == {"job_id": str(job_id), "after": None}

    assert await env.run_batch(job_id, None) == 2
    assert await env.run_batch(job_id, None) == 0  # повтор доставки той же порции
    next_task = env.tasks.tasks[-1]
    after = uuid.UUID(next_task["payload"]["after"])
    assert await env.run_batch(job_id, after) == 1
    assert env.store.republish[job_id].status is RepublishStatus.DONE
    assert env.store.republish[job_id].published == 3

    for item_id, item in env.store.items.items():
        expected = versions[item_id] + (1 if item.kind is MediaKind.FILM else 0)
        assert item.version == expected
    republished = [s for s in env.snapshots() if s.get("changed_fields") == ["republish"]]
    assert len(republished) == 3
    assert env.store.actions[(ACTOR, "k-republish-01")].action == "items.republish"
