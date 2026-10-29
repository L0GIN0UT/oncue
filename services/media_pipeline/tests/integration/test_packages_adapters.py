"""Пакетная загрузка на реальной инфраструктуре: потоковый S3 (multipart) и репозиторий пакетов."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.ports import StorageArea
from media_pipeline_service.domain.packages import ImportPackage, PackageStatus
from media_pipeline_service.infrastructure.db.repositories import SqlMediaUnitOfWork
from media_pipeline_service.infrastructure.storage import S3Storage
from media_pipeline_service.main import DEFINITION
from media_pipeline_service.settings import S3Settings
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gv_core import uuid7
from gv_db import create_engine, create_session_factory
from gv_testkit import FakeClock, S3Endpoint, apply_migrations

pytestmark = pytest.mark.integration

MB = 1024 * 1024
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------- S3
def make_storage(endpoint: S3Endpoint) -> S3Storage:
    suffix = uuid7().hex[-12:]
    return S3Storage(
        S3Settings(
            endpoint=endpoint.url,
            access_key=SecretStr(endpoint.access_key),
            secret_key=SecretStr(endpoint.secret_key),
            bucket_sources=f"gv-src-{suffix}",
            bucket_assets=f"gv-assets-{suffix}",
        ),
        part_size=5 * MB,  # минимум S3
    )


async def pieces(data: bytes, size: int = MB) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


async def pending_uploads(storage: S3Storage) -> list[object]:
    async with storage._client() as s3:
        reply = await s3.list_multipart_uploads(Bucket=storage.bucket(StorageArea.SOURCES))
    return list(reply.get("Uploads", []))


async def test_put_stream_multipart_roundtrip_and_download(
    s3_endpoint: S3Endpoint, tmp_path: Path
) -> None:
    storage = make_storage(s3_endpoint)
    await storage.ensure_buckets()
    data = os.urandom(12 * MB + 123)  # три части: 5 + 5 + хвост
    await storage.put_stream(StorageArea.SOURCES, "k/big.zip", pieces(data), "application/zip")

    async with storage._client() as s3:
        head = await s3.head_object(Bucket=storage.bucket(StorageArea.SOURCES), Key="k/big.zip")
    assert (head["ContentLength"], head["ContentType"]) == (len(data), "application/zip")
    assert await pending_uploads(storage) == []

    target = tmp_path / "big.zip"
    await storage.download_to(StorageArea.SOURCES, "k/big.zip", target, max_bytes=len(data))
    assert await asyncio.to_thread(target.read_bytes) == data


async def test_put_stream_small_body_and_exact_part_boundary(s3_endpoint: S3Endpoint) -> None:
    storage = make_storage(s3_endpoint)
    await storage.ensure_buckets()
    for name, size in (("small", 10), ("empty", 0), ("exact", 5 * MB), ("exact2", 10 * MB)):
        data = os.urandom(size)
        await storage.put_stream(StorageArea.SOURCES, f"k/{name}", pieces(data), "application/zip")
        assert await storage.get(StorageArea.SOURCES, f"k/{name}", max_bytes=size + 1) == data
    assert await pending_uploads(storage) == []


async def test_put_stream_source_failure_aborts_multipart(s3_endpoint: S3Endpoint) -> None:
    storage = make_storage(s3_endpoint)
    await storage.ensure_buckets()

    async def failing() -> AsyncIterator[bytes]:
        yield os.urandom(6 * MB)  # часть уже ушла в S3
        raise ValueError("клиент оборвал соединение")

    with pytest.raises(ValueError, match="оборвал"):
        await storage.put_stream(StorageArea.SOURCES, "k/broken.zip", failing(), "application/zip")
    assert await pending_uploads(storage) == []
    with pytest.raises(GenerationError) as exc:
        await storage.get(StorageArea.SOURCES, "k/broken.zip", max_bytes=100 * MB)
    assert exc.value.code == "source.missing"


async def test_download_limits_and_missing_object(s3_endpoint: S3Endpoint, tmp_path: Path) -> None:
    storage = make_storage(s3_endpoint)
    await storage.ensure_buckets()
    await storage.put_stream(
        StorageArea.SOURCES, "k/a.zip", pieces(os.urandom(2 * MB)), "application/zip"
    )
    with pytest.raises(GenerationError) as exc:
        await storage.download_to(StorageArea.SOURCES, "k/a.zip", tmp_path / "a", max_bytes=MB)
    assert exc.value.code == "source.too_large"
    with pytest.raises(GenerationError) as exc:
        await storage.download_to(StorageArea.SOURCES, "k/none.zip", tmp_path / "b", max_bytes=MB)
    assert exc.value.code == "source.missing"


# ---------------------------------------------------------------- PostgreSQL
@pytest.fixture
async def sessions(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    engine = create_engine(database_url)
    yield create_session_factory(engine)
    await engine.dispose()


def uow(sessions: async_sessionmaker[AsyncSession]) -> SqlMediaUnitOfWork:
    return SqlMediaUnitOfWork(producer="media_pipeline", clock=FakeClock(NOW), sessions=sessions)


def new_package() -> ImportPackage:
    return ImportPackage(
        uuid7(),
        actor_id=uuid7(),
        reason="пакет",
        archive_key="k/" + "a" * 32 + ".zip",
        archive_sha256="b" * 64,
        archive_bytes=1234,
        created_at=NOW,
    )


async def test_package_repository_roundtrip_counters_and_errors(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    package = new_package()
    async with uow(sessions) as u:
        await u.packages.add(package)
        await u.commit()

    package.start(NOW)
    package.set_total(3)
    package.record_done(source_missing=True, license_missing=False)
    package.record_failed(
        item_key="film:tmdb-1",
        path="a/x.jpg",
        role="frame",
        code="source.unsupported",
        message="файл не подходит",
        keep_errors=1,
    )
    package.record_failed(  # сверх лимита хранения - только счётчик
        item_key="film:tmdb-1",
        path="a/y.jpg",
        role="frame",
        code="source.unsupported",
        message="файл не подходит",
        keep_errors=1,
    )
    async with uow(sessions) as u:
        await u.packages.save(package)
        await u.commit()

    async with uow(sessions) as u:
        loaded = await u.packages.get(package.id)
    assert loaded is not None
    assert (loaded.status, loaded.total, loaded.done, loaded.failed) == (
        PackageStatus.PROCESSING,
        3,
        1,
        2,
    )
    assert (loaded.missing_source, loaded.missing_license, loaded.errors_dropped) == (1, 0, 1)
    assert [(e.seq, e.path, e.code) for e in loaded.errors] == [
        (1, "a/x.jpg", "source.unsupported")
    ]

    loaded.finish(NOW)
    async with uow(sessions) as u:
        await u.packages.save(loaded)
        await u.commit()
    async with uow(sessions) as u:
        done = await u.packages.get(package.id)
    assert done is not None and done.status is PackageStatus.PARTIAL and done.archive_key is None

    # перезапуск обработки стирает старые ошибки
    done.status = PackageStatus.QUEUED
    done.start(NOW)
    async with uow(sessions) as u:
        await u.packages.save(done)
        await u.commit()
    async with uow(sessions) as u:
        restarted = await u.packages.get(package.id)
    assert restarted is not None and restarted.errors == [] and restarted.failed == 0


async def test_package_schema_rejects_inconsistent_rows(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    package = new_package()
    async with uow(sessions) as u:
        await u.packages.add(package)
        await u.commit()
    for statement in (
        "UPDATE import_packages SET status = 'weird'",
        "UPDATE import_packages SET total = 1, done = 1, failed = 1",
        "UPDATE import_packages SET done = -1",
    ):
        async with sessions() as session:
            with pytest.raises(IntegrityError):
                await session.execute(text(statement))
                await session.commit()


async def test_package_errors_are_deleted_with_package(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    package = new_package()
    package.set_total(1)
    package.record_failed(
        item_key="k", path="p", role="frame", code="c", message="m", keep_errors=5
    )
    async with uow(sessions) as u:
        await u.packages.add(package)
        await u.packages.save(package)
        await u.commit()
    async with sessions() as session:
        await session.execute(text("DELETE FROM import_packages"))
        await session.commit()
        left = (await session.execute(text("SELECT count(*) FROM import_package_errors"))).scalar()
    assert left == 0
