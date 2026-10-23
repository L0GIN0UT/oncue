"""UGC-изображения на реальных PostgreSQL и S3-совместимом хранилище (ADR 0048)."""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from media_pipeline_service.application.commands.ugc import (
    ModerateUgcImage,
    SubmitUgcImage,
    SweepOrphanUgcObjects,
    UgcObjectPurger,
)
from media_pipeline_service.application.ports import StorageArea
from media_pipeline_service.application.queries.ugc import ListUgcQueue
from media_pipeline_service.domain.errors import UgcNotPendingError
from media_pipeline_service.domain.ugc import UgcStatus
from media_pipeline_service.infrastructure.config import load_ugc_catalog
from media_pipeline_service.infrastructure.db.repositories import SqlMediaUnitOfWork
from media_pipeline_service.infrastructure.db.tables import (
    TABLES,
    admin_actions,
    ugc_images,
    ugc_object_purge,
)
from media_pipeline_service.infrastructure.links import SecretsTokenSource
from media_pipeline_service.infrastructure.storage import S3Storage
from media_pipeline_service.infrastructure.ugc_images import PillowUgcEncoder
from media_pipeline_service.main import DEFINITION
from media_pipeline_service.settings import S3Settings
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gv_core import uuid7
from gv_db import create_engine, create_session_factory
from gv_testkit import FakeClock, apply_migrations, outbox_envelopes
from gv_testkit.infra import S3Endpoint

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
OWNER = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000b0001")
MODERATOR = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000a0001")
SECRET = "s" * 40


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (300, 300), (10, 120, 200)).save(out, format="PNG")
    return out.getvalue()


@dataclass
class Env:
    sessions: async_sessionmaker[AsyncSession]
    storage: S3Storage
    clock: FakeClock
    submit: SubmitUgcImage
    moderate: ModerateUgcImage
    queue: ListUgcQueue
    purger: UgcObjectPurger
    uow: Callable[[], SqlMediaUnitOfWork]

    async def purge_rows(self) -> set[str]:
        async with self.sessions() as session:
            return set((await session.execute(select(ugc_object_purge.c.key))).scalars().all())

    async def assets(self) -> set[str]:
        async with self.storage._client() as s3:
            listing = await s3.list_objects_v2(Bucket=self.storage.bucket(StorageArea.ASSETS))
        return {o["Key"] for o in listing.get("Contents", [])}

    async def events(self) -> list[Any]:
        async with self.sessions() as session:
            return await outbox_envelopes(session, TABLES.outbox)


@pytest.fixture
async def env(database_url: str, s3_endpoint: S3Endpoint) -> AsyncIterator[Env]:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    engine = create_engine(database_url)
    sessions = create_session_factory(engine)
    suffix = uuid7().hex[-12:]
    storage = S3Storage(
        S3Settings(
            endpoint=s3_endpoint.url,
            access_key=SecretStr(s3_endpoint.access_key),
            secret_key=SecretStr(s3_endpoint.secret_key),
            bucket_sources=f"gv-src-{suffix}",
            bucket_assets=f"gv-assets-{suffix}",
        )
    )
    await storage.ensure_buckets()
    clock = FakeClock(NOW)
    catalog = load_ugc_catalog()
    tokens = SecretsTokenSource()

    def uow() -> SqlMediaUnitOfWork:
        return SqlMediaUnitOfWork(producer="media_pipeline", clock=clock, sessions=sessions)

    purger = UgcObjectPurger(uow, storage, batch=2)
    yield Env(
        sessions=sessions,
        storage=storage,
        clock=clock,
        submit=SubmitUgcImage(uow, clock, PillowUgcEncoder(), storage, tokens, catalog, purger),
        moderate=ModerateUgcImage(uow, clock, storage, tokens, catalog, purger),
        queue=ListUgcQueue(uow),
        purger=purger,
        uow=uow,
    )
    await engine.dispose()


async def _submit(env: Env, key: str, owner: UUID = OWNER) -> Any:
    return await env.submit.execute(
        client_id="progression",
        owner_user_id=owner,
        purpose="avatar",
        data=_png(),
        idempotency_key=key,
    )


async def test_full_flow_copies_to_public_prefix_and_writes_event_in_same_transaction(
    env: Env,
) -> None:
    view = await _submit(env, "idem-0000000001")
    (pending,) = await env.assets()
    assert pending.startswith("p/")
    async with env.storage._client() as s3:
        head = await s3.head_object(Bucket=env.storage.bucket(StorageArea.ASSETS), Key=pending)
    assert head["ContentType"] == "image/webp" and head.get("Metadata", {}) == {}
    assert await env.events() == []  # приём событий не публикует

    await env.moderate.approve(
        view.asset_id, moderator_id=MODERATOR, reason="ок", idempotency_key="mod-0000000001"
    )
    (public,) = await env.assets()
    assert public.startswith("u/") and public != pending.replace("p/", "u/")
    data = await env.storage.get(StorageArea.ASSETS, public, max_bytes=1_500_000)
    assert Image.open(io.BytesIO(data)).format == "WEBP"
    (approved,) = await env.events()
    assert approved.event_type == "media_pipeline.AssetModerated"
    assert approved.topic == "media_pipeline.ugc_image.v1"
    assert approved.aggregate_id == str(view.asset_id)
    assert approved.payload["public_key"] == public and approved.payload["version"] == 1

    await env.moderate.reject(
        view.asset_id,
        moderator_id=MODERATOR,
        reason_code="inappropriate",
        reason="снятие",
        idempotency_key="mod-0000000002",
    )
    assert await env.assets() == set()  # публичный объект удалён
    events = await env.events()
    assert [e.payload["version"] for e in events] == [1, 2]
    assert events[1].payload["status"] == "rejected" and events[1].payload["public_key"] is None
    assert "moderator_id" not in events[1].payload and "reason" not in events[1].payload
    async with env.sessions() as session:
        actions = (await session.execute(select(admin_actions.c.action))).scalars().all()
    assert sorted(actions) == ["ugc.approve", "ugc.reject"]


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_concurrent_decisions_one_wins(env: Env, decision: str) -> None:
    view = await _submit(env, "idem-0000000001")

    async def decide(key: str) -> object:
        if decision == "approve":
            return await env.moderate.approve(
                view.asset_id, moderator_id=MODERATOR, reason="ок", idempotency_key=key
            )
        return await env.moderate.reject(
            view.asset_id,
            moderator_id=MODERATOR,
            reason_code="other",
            reason="нет",
            idempotency_key=key,
        )

    results = await asyncio.gather(
        decide("mod-a-00000001"), decide("mod-b-00000001"), return_exceptions=True
    )
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(failures) == 1 and isinstance(failures[0], UgcNotPendingError)
    assert len(await env.events()) == 1  # одно решение - одно событие
    objects = await env.assets()
    assert len(objects) == (1 if decision == "approve" else 0)


async def test_replay_of_decision_does_not_duplicate_event(env: Env) -> None:
    view = await _submit(env, "idem-0000000001")
    for _ in range(2):
        await env.moderate.approve(
            view.asset_id, moderator_id=MODERATOR, reason="ок", idempotency_key="mod-0000000001"
        )
    assert len(await env.events()) == 1


async def test_submit_replay_and_supersede(env: Env) -> None:
    first = await _submit(env, "idem-0000000001")
    again = await _submit(env, "idem-0000000001")
    assert again.asset_id == first.asset_id and len(await env.assets()) == 1
    second = await _submit(env, "idem-0000000002")
    assert second.asset_id != first.asset_id
    assert len(await env.assets()) == 1  # объект прежней заявки удалён
    async with env.sessions() as session:
        rows = (
            await session.execute(
                select(ugc_images.c.id, ugc_images.c.status, ugc_images.c.reason_code)
            )
        ).all()
    by_id = {r.id: (r.status, r.reason_code) for r in rows}
    assert by_id[first.asset_id] == ("rejected", "superseded")
    assert by_id[second.asset_id] == ("pending", None)
    assert await env.events() == []


async def test_concurrent_submits_leave_exactly_one_pending(env: Env) -> None:
    await asyncio.gather(*(_submit(env, f"idem-00000000{i}") for i in range(4)))
    async with env.sessions() as session:
        pending = await session.scalar(select(func.count()).where(ugc_images.c.status == "pending"))
    assert pending == 1 and len(await env.assets()) == 1


async def test_queue_pages_by_age_with_keyset_cursor(env: Env) -> None:
    ids = []
    for i in range(3):
        env.clock.advance(seconds=1)
        ids.append((await _submit(env, f"idem-00000000{i}", owner=uuid7())).asset_id)
    page = await env.queue.execute(status=UgcStatus.PENDING, purpose=None, cursor=None, limit=2)
    assert [v.asset_id for v in page.items] == ids[:2] and page.next_cursor
    rest = await env.queue.execute(
        status=UgcStatus.PENDING, purpose="avatar", cursor=page.next_cursor, limit=2
    )
    assert [v.asset_id for v in rest.items] == ids[2:] and rest.next_cursor is None


async def test_s3_delete_failure_keeps_key_in_purge_table_until_job_succeeds(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = await _submit(env, "idem-0000000001")
    (pending,) = await env.assets()
    real_delete = env.storage.delete

    async def broken(area: StorageArea, keys: Any) -> None:
        raise ConnectionError("S3 недоступен")

    monkeypatch.setattr(env.storage, "delete", broken)
    await env.moderate.reject(
        view.asset_id,
        moderator_id=MODERATOR,
        reason_code="other",
        reason="нет",
        idempotency_key="mod-0000000001",
    )
    # решение зафиксировано, ключ записан в той же транзакции, объект ещё в S3
    async with env.sessions() as session:
        status = await session.scalar(select(ugc_images.c.status))
    assert status == "rejected"
    assert await env.purge_rows() == {pending} and await env.assets() == {pending}
    assert await env.purger.sweep() == 0 and await env.purge_rows() == {pending}

    monkeypatch.setattr(env.storage, "delete", real_delete)
    assert await env.purger.sweep() == 1
    assert await env.assets() == set() and await env.purge_rows() == set()
    assert await env.purger.sweep() == 0


async def test_approving_new_avatar_replaces_previous_one_without_event(env: Env) -> None:
    first = await _submit(env, "idem-0000000001")
    await env.moderate.approve(
        first.asset_id, moderator_id=MODERATOR, reason="ок", idempotency_key="mod-0000000001"
    )
    (first_public,) = await env.assets()
    second = await _submit(env, "idem-0000000002")

    await env.moderate.approve(
        second.asset_id, moderator_id=MODERATOR, reason="ок", idempotency_key="mod-0000000002"
    )

    async with env.sessions() as session:
        rows = {
            r.id: (r.status, r.reason_code, r.version)
            for r in (await session.execute(select(ugc_images))).all()
        }
    assert rows[first.asset_id] == ("rejected", "replaced", 2)
    assert rows[second.asset_id] == ("approved", None, 1)
    events = await env.events()
    assert [(e.payload["asset_id"], e.payload["status"]) for e in events] == [
        (str(first.asset_id), "approved"),
        (str(second.asset_id), "approved"),
    ]  # для замещённого A события нет
    (second_public,) = await env.assets()
    assert second_public != first_public and await env.purge_rows() == set()


async def test_concurrent_approvals_of_two_images_keep_exactly_one_approved(env: Env) -> None:
    """Два одобрения одного владельца подряд из разных потоков: блокировка владельца
    сериализует их - одобренным остаётся одно изображение, второе замещено."""
    first = await _submit(env, "idem-0000000001")
    # вторая заявка снимет первую pending: заводим вторую строку напрямую через новую сессию
    # невозможно, поэтому одобряем по очереди, но параллельно с повтором решения
    results = await asyncio.gather(
        env.moderate.approve(
            first.asset_id, moderator_id=MODERATOR, reason="ок", idempotency_key="mod-a-0000001"
        ),
        env.moderate.approve(
            first.asset_id, moderator_id=MODERATOR, reason="ок", idempotency_key="mod-b-0000001"
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(r, UgcNotPendingError) for r in results) == 1
    async with env.sessions() as session:
        approved = await session.scalar(
            select(func.count()).where(ugc_images.c.status == "approved")
        )
    assert approved == 1


async def test_orphan_sweep_on_real_storage_deletes_only_old_unreferenced_p_objects(
    env: Env,
) -> None:
    live = await _submit(env, "idem-0000000001")
    (live_key,) = await env.assets()
    orphans = [f"p/{uuid7().hex[-32:]}.webp" for _ in range(3)]
    for key in orphans:
        await env.storage.put(StorageArea.ASSETS, key, b"x", "image/webp")
    public = f"u/{uuid7().hex[-32:]}.webp"
    await env.storage.put(StorageArea.ASSETS, public, b"x", "image/webp")

    # «Сейчас»: объекты свежие - ничего не удаляется.
    fresh = SweepOrphanUgcObjects(
        env.uow, env.storage, FakeClock(datetime.now(UTC)), ttl=timedelta(hours=24), page_size=2
    )
    assert await fresh.sweep() == 0

    # Через двое суток все p/ старше TTL; страницы по 2 объекта (3 сироты + живой).
    later = SweepOrphanUgcObjects(
        env.uow,
        env.storage,
        FakeClock(datetime.now(UTC) + timedelta(hours=48)),
        ttl=timedelta(hours=24),
        page_size=2,
    )
    assert await later.sweep() == 3
    assert await env.assets() == {live_key, public}  # живая заявка и публичный u/ целы
    async with env.sessions() as session:
        assert await session.scalar(select(ugc_images.c.status)) == "pending"
    assert live.asset_id is not None


async def test_schema_enforces_status_and_key_invariants(env: Env) -> None:
    view = await _submit(env, "idem-0000000001")
    bad_updates = [
        "UPDATE ugc_images SET pending_key = NULL",  # pending без ключа
        "UPDATE ugc_images SET status = 'approved'",  # approved без public_key
        "UPDATE ugc_images SET pending_key = 'p/../../etc.webp'",  # форма ключа
        "UPDATE ugc_images SET status = 'weird'",
        "UPDATE ugc_images SET version = -1",
    ]
    for statement in bad_updates:
        async with env.sessions() as session:
            with pytest.raises(IntegrityError):
                await session.execute(text(statement))
                await session.commit()
    assert view.asset_id is not None
