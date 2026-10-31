"""Пакетная загрузка: приём архива и разбор задачей на фейках портов (фикстура ``media``)."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from media_pipeline_service.application.commands.generation import TaskNotVisibleError
from media_pipeline_service.application.commands.packages import PackageTooLargeError
from media_pipeline_service.application.dto import GENERATE_TASK, PACKAGE_TASK
from media_pipeline_service.application.ports import StorageArea
from media_pipeline_service.domain.packages import PackageRejectedError, PackageStatus
from media_pipeline_service.domain.sources import SourceKind, SourceOrigin

from gv_core import uuid7
from gv_core.errors import ConflictError

from .test_use_cases import ACTOR, ITEM, JPEG, snapshot

pytestmark = pytest.mark.unit

OTHER_ITEM = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000999")


def key() -> str:
    return f"key-{uuid7().hex}"


def zip_of(
    files: dict[str, bytes], manifest: Any = None, *, raw_manifest: bytes | None = None
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        if raw_manifest is not None:
            zf.writestr("manifest.json", raw_manifest)
        elif manifest is not None:
            zf.writestr("manifest.json", json.dumps(manifest))
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def film(files: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "key": "film:tmdb-550",
        "kind": "film",
        "media_item_id": str(ITEM),
        "files": files,
        **extra,
    }


def doc(*items: dict[str, Any]) -> dict[str, Any]:
    return {"version": 1, "items": list(items)}


async def chunks(data: bytes, size: int = 64) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


async def submit(
    media: Any, archive: bytes, *, idem: str | None = None, reason: str = "пакет"
) -> Any:
    return await media.submit_package.execute(
        body=chunks(archive),
        declared_length=len(archive),
        actor_id=ACTOR,
        reason=reason,
        idempotency_key=idem or key(),
    )


async def run(media: Any, archive: bytes, **kw: Any) -> Any:
    submitted = await submit(media, archive, **kw)
    await media.process_package.execute(submitted.package_id)
    return await media.get_package.execute(submitted.package_id)


@pytest.fixture
async def with_film(media: Any) -> Any:
    await media.sync.apply_snapshot(snapshot(sources=()))
    return media


# ---------------------------------------------------------------- приём
async def test_submit_returns_package_id_and_enqueues_task(with_film: Any) -> None:
    media = with_film
    archive = zip_of({"a.jpg": JPEG}, doc(film([{"role": "frame", "path": "a.jpg"}])))
    result = await submit(media, archive)
    assert result.status is PackageStatus.QUEUED
    (package,) = media.db.packages.values()
    assert package.id == result.package_id and package.archive_bytes == len(archive)
    (task,) = [t for t in media.queue.tasks if t["task_type"] == PACKAGE_TASK]
    assert task["task_id"] == str(result.package_id)
    assert media.storage.objects[(StorageArea.SOURCES, package.archive_key)][0] == archive
    assert media.db.audit[-1].action == "package.submit"


async def test_submit_is_idempotent_by_key(with_film: Any) -> None:
    media = with_film
    archive = zip_of({"a.jpg": JPEG}, doc(film([{"role": "frame", "path": "a.jpg"}])))
    idem = key()
    first = await submit(media, archive, idem=idem)
    again = await submit(media, archive, idem=idem)
    assert again.package_id == first.package_id
    assert len(media.db.packages) == 1
    assert len([t for t in media.queue.tasks if t["task_type"] == PACKAGE_TASK]) == 1
    # Лишняя копия архива от повтора удалена.
    assert len(media.storage.keys(StorageArea.SOURCES)) == 1


async def test_same_key_with_other_archive_conflicts_and_leaves_no_object(with_film: Any) -> None:
    media = with_film
    idem = key()
    await submit(media, zip_of({"a.jpg": JPEG}), idem=idem)
    with pytest.raises(ConflictError) as exc:
        await submit(media, zip_of({"b.jpg": JPEG}), idem=idem)
    assert exc.value.code == "idempotency.key_reused"
    assert len(media.storage.keys(StorageArea.SOURCES)) == 1


async def test_submit_rejects_non_zip_and_leaves_no_package(with_film: Any) -> None:
    media = with_film
    for body in (b"MZ\x90\x00 not a zip", b"", b"PK"):
        with pytest.raises(PackageRejectedError) as exc:
            await submit(media, body)
        assert exc.value.code == "package.not_zip"
    assert media.db.packages == {}
    assert media.storage.keys(StorageArea.SOURCES) == []


async def test_submit_rejects_oversize_by_header_and_by_stream(with_film: Any) -> None:
    media = with_film
    limit = media.package_limits.max_archive_bytes
    with pytest.raises(PackageTooLargeError):
        await media.submit_package.execute(
            body=chunks(b""),
            declared_length=limit + 1,
            actor_id=ACTOR,
            reason="r",
            idempotency_key=key(),
        )
    liar = b"PK\x03\x04" + b"\x00" * (limit + 10)
    with pytest.raises(PackageTooLargeError):
        await media.submit_package.execute(
            body=chunks(liar, 100_000),
            declared_length=10,
            actor_id=ACTOR,
            reason="r",
            idempotency_key=key(),
        )
    assert media.db.packages == {} and media.storage.keys(StorageArea.SOURCES) == []


async def test_worker_retries_while_package_is_not_visible(with_film: Any) -> None:
    with pytest.raises(TaskNotVisibleError):
        await with_film.process_package.execute(uuid7())


# ---------------------------------------------------------------- разбор
async def test_package_files_go_through_regular_upload(with_film: Any) -> None:
    media = with_film
    archive = zip_of(
        {"f/1.jpg": JPEG, "f/2.jpg": JPEG + b"2", "poster.png": JPEG + b"p"},
        doc(
            film(
                [
                    {"role": "frame", "path": "f/1.jpg"},
                    {"role": "frame", "path": "f/2.jpg"},
                    {"role": "poster", "path": "poster.png"},
                ],
                source_url="https://example.org/film",
                license_note="CC0",
            )
        ),
    )
    package = await run(media, archive)
    assert (package.status, package.total, package.done, package.failed) == (
        PackageStatus.DONE,
        3,
        3,
        0,
    )
    assert (package.missing_source, package.missing_license) == (0, 0)
    sources = sorted(media.db.sources.values(), key=lambda s: (s.kind.value, s.position))
    assert [(s.kind, s.position) for s in sources] == [
        (SourceKind.FRAME, 1),
        (SourceKind.FRAME, 2),
        (SourceKind.POSTER, 1),
    ]
    assert {s.origin for s in sources} == {SourceOrigin.UPLOAD}
    assert {(s.source_url, s.license_note) for s in sources} == {
        ("https://example.org/film", "CC0")
    }
    # тот же use-case приёма: аудит ручной загрузки и планирование наборов
    assert [a.action for a in media.db.audit].count("source.upload") == 3
    assert any(t["task_type"] == GENERATE_TASK for t in media.queue.tasks)
    # архив убран из хранилища после обработки
    assert package.archive_key is None
    assert not [k for k in media.storage.keys(StorageArea.SOURCES) if k.startswith("k/")]


async def test_empty_license_and_source_are_not_errors(with_film: Any) -> None:
    media = with_film
    for extra in ({}, {"license_note": ""}, {"license_note": None, "source_url": ""}):
        media.db.sources.clear()
        archive = zip_of(
            {"a.jpg": JPEG + bytes(str(extra), "utf-8")},
            doc(film([{"role": "frame", "path": "a.jpg"}], **extra)),
        )
        package = await run(media, archive)
        assert (package.status, package.failed) == (PackageStatus.DONE, 0)
        assert (package.missing_source, package.missing_license) == (1, 1)
        (source,) = media.db.sources.values()
        assert source.license_note is None and source.source_url is None
        assert source.origin is SourceOrigin.UPLOAD


async def test_one_bad_file_does_not_stop_the_rest(with_film: Any) -> None:
    media = with_film
    archive = zip_of(
        {
            "ok1.jpg": JPEG,
            "fake.jpg": b"MZ\x90\x00 this is an executable, not a jpeg",
            "ok2.jpg": JPEG + b"2",
        },
        doc(
            film(
                [
                    {"role": "frame", "path": "ok1.jpg"},
                    {"role": "frame", "path": "fake.jpg"},
                    {"role": "frame", "path": "missing.jpg"},
                    {"role": "audio", "path": "ok2.jpg"},
                    {"role": "frame", "path": "ok2.jpg"},
                ]
            )
        ),
    )
    package = await run(media, archive)
    assert (package.status, package.total, package.done, package.failed) == (
        PackageStatus.PARTIAL,
        5,
        2,
        3,
    )
    by_path = {(e.path, e.role): e.code for e in package.errors}
    assert by_path[("fake.jpg", "frame")].startswith("source.")  # расширение не доверяем
    assert by_path[("missing.jpg", "frame")] == "package.file_missing"
    assert by_path[("ok2.jpg", "audio")] == "source.kind_not_applicable"
    assert {e.item_key for e in package.errors} == {"film:tmdb-550"}
    assert len(media.db.sources) == 2


async def test_file_type_is_detected_by_content_not_extension(with_film: Any) -> None:
    media = with_film
    archive = zip_of({"frame.txt": JPEG}, doc(film([{"role": "frame", "path": "frame.txt"}])))
    package = await run(media, archive)
    assert (package.status, package.done) == (PackageStatus.DONE, 1)


async def test_resubmitting_same_package_does_not_duplicate_sources(with_film: Any) -> None:
    media = with_film
    archive = zip_of({"a.jpg": JPEG}, doc(film([{"role": "frame", "path": "a.jpg"}])))
    first = await run(media, archive)
    second = await run(media, archive)  # тот же архив, новый Idempotency-Key
    assert first.id != second.id
    assert (second.status, second.done) == (PackageStatus.DONE, 1)
    assert len(media.db.sources) == 1
    assert [a.action for a in media.db.audit].count("source.upload") == 1


async def test_reprocessing_after_worker_crash_does_not_duplicate_sources(with_film: Any) -> None:
    media = with_film
    archive = zip_of(
        {"a.jpg": JPEG, "b.jpg": JPEG + b"b"},
        doc(film([{"role": "frame", "path": "a.jpg"}, {"role": "frame", "path": "b.jpg"}])),
    )
    submitted = await submit(media, archive)
    await media.process_package.execute(submitted.package_id)
    # Воркер «упал» после обработки, до подтверждения задачи: пакет снова в работе, архив на месте.
    package = media.db.packages[submitted.package_id]
    key_ = "k/" + "e" * 32 + ".zip"
    await media.storage.put(StorageArea.SOURCES, key_, archive, "application/zip")
    package.status, package.archive_key = PackageStatus.QUEUED, key_
    await media.process_package.execute(submitted.package_id)
    assert len(media.db.sources) == 2
    done = await media.get_package.execute(submitted.package_id)
    assert (done.status, done.done) == (PackageStatus.DONE, 2)


async def test_terminal_package_is_not_processed_again(with_film: Any) -> None:
    media = with_film
    archive = zip_of({"a.jpg": JPEG}, doc(film([{"role": "frame", "path": "a.jpg"}])))
    package = await run(media, archive)
    await media.process_package.execute(package.id)
    assert len(media.db.sources) == 1 and package.status is PackageStatus.DONE


async def test_item_level_errors(with_film: Any) -> None:
    media = with_film
    track = {
        "key": "track:demo-1",
        "kind": "track",
        "media_item_id": str(ITEM),  # единица - фильм
        "files": [{"role": "audio", "path": "a.jpg"}],
    }
    no_id = {"key": "film:no-id", "kind": "film", "files": [{"role": "frame", "path": "a.jpg"}]}
    unknown = film(
        [{"role": "frame", "path": "a.jpg"}], media_item_id=str(OTHER_ITEM), key="film:ghost"
    )
    package = await run(media, zip_of({"a.jpg": JPEG}, doc(track, no_id, unknown)))
    assert (package.status, package.done, package.failed) == (PackageStatus.FAILED, 0, 3)
    assert {e.item_key: e.code for e in package.errors} == {
        "track:demo-1": "package.kind_mismatch",
        "film:no-id": "package.media_item_unresolved",
        "film:ghost": "media_item.not_found",
    }


async def test_errors_are_capped_but_counted(with_film: Any) -> None:
    media = with_film
    files = [{"role": "frame", "path": f"missing{i}.jpg"} for i in range(6)]
    package = await run(media, zip_of({}, doc(film(files))))
    assert package.failed == 6
    assert len(package.errors) == 3 and package.errors_dropped == 3  # keep_errors в harness - 3


# ---------------------------------------------------------------- пакет отвергнут целиком
@pytest.mark.parametrize(
    ("archive", "code"),
    [
        (zip_of({"a.jpg": JPEG}), "package.manifest_missing"),
        (zip_of({}, raw_manifest=b"{not json"), "package.manifest_invalid"),
        (zip_of({}, {"version": 1, "items": []}), "package.manifest_invalid"),
        (
            zip_of({"../evil.jpg": JPEG}, doc(film([{"role": "frame", "path": "a.jpg"}]))),
            "package.unsafe_path",
        ),
        (zip_of({}, raw_manifest=b"x" * 200_000), "package.manifest_invalid"),
    ],
)
async def test_bad_archive_or_manifest_fails_whole_package(
    with_film: Any, archive: bytes, code: str
) -> None:
    package = await run(with_film, archive)
    assert package.status is PackageStatus.FAILED
    assert package.error_code in {code, "package.entry_too_large"}
    assert package.done == 0 and with_film.db.sources == {}
    assert package.archive_key is None
    assert not [k for k in with_film.storage.keys(StorageArea.SOURCES) if k.startswith("k/")]


async def test_zip_bomb_is_rejected_without_extracting(with_film: Any) -> None:
    media = with_film
    bomb = zip_of(
        {"bomb.bin": b"\x00" * 90_000}, doc(film([{"role": "frame", "path": "bomb.bin"}]))
    )
    package = await run(media, bomb)
    assert package.status is PackageStatus.FAILED
    assert package.error_code == "package.ratio_exceeded"
    assert media.db.sources == {}


# ---------------------------------------------------------------- устойчивость воркера
async def test_unexpected_crash_fails_package_and_removes_archive(with_film: Any) -> None:
    media = with_film
    archive = zip_of({"a.jpg": JPEG}, doc(film([{"role": "frame", "path": "a.jpg"}])))
    submitted = await submit(media, archive)

    async def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("неожиданный сбой")

    media.process_package._manifest = boom
    await media.process_package.execute(submitted.package_id)
    package = await media.get_package.execute(submitted.package_id)
    assert (package.status, package.error_code) == (PackageStatus.FAILED, "package.internal_error")
    assert "неожиданный" not in (package.error_message or "")
    assert not [k for k in media.storage.keys(StorageArea.SOURCES) if k.startswith("k/")]


async def test_live_worker_blocks_second_claim_until_lease_expires(with_film: Any) -> None:
    media = with_film
    archive = zip_of({"a.jpg": JPEG}, doc(film([{"role": "frame", "path": "a.jpg"}])))
    submitted = await submit(media, archive)
    package = media.db.packages[submitted.package_id]
    package.status, package.heartbeat_at = PackageStatus.PROCESSING, media.clock.now()
    with pytest.raises(TaskNotVisibleError):  # задачу вернут в очередь, не потеряют
        await media.process_package.execute(submitted.package_id)
    assert media.db.sources == {}

    media.clock.advance(seconds=301)  # воркер молчит дольше lease - пакет можно забрать
    await media.process_package.execute(submitted.package_id)
    done = await media.get_package.execute(submitted.package_id)
    assert (done.status, done.done) == (PackageStatus.DONE, 1)


async def test_orphan_archives_are_swept_only_when_old_and_unreferenced(with_film: Any) -> None:
    media = with_film
    submitted = await submit(media, zip_of({"a.jpg": JPEG}))
    live = media.db.packages[submitted.package_id].archive_key
    old, fresh = "k/" + "1" * 32 + ".zip", "k/" + "2" * 32 + ".zip"
    for orphan in (old, fresh, live):
        await media.storage.put(StorageArea.SOURCES, orphan, b"PK", "application/zip")
    long_ago = media.clock.now() - timedelta(days=3)
    media.storage.modified.update({old: long_ago, live: long_ago, fresh: media.clock.now()})
    assert await media.sweep_package_orphans.sweep() == 1
    left = media.storage.keys(StorageArea.SOURCES)
    assert old not in left and fresh in left and live in left
