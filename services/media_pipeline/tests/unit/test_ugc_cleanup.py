"""Очистка объектов UGC: гарантированное удаление, замена прежнего аватара, осиротевшие p/."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from media_pipeline_service.application.ports import StorageArea
from media_pipeline_service.domain.errors import UgcNotPendingError
from media_pipeline_service.domain.ugc import (
    REPLACED_REASON,
    AssetModerated,
    UgcImage,
    UgcStatus,
    pending_key,
    public_key,
)

from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

OWNER = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000b0001")
MODERATOR = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000a0001")
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
REASON = "проверка"


async def submit(media: Any, key: str, owner: UUID = OWNER) -> Any:
    return await media.ugc.submit.execute(
        client_id="progression",
        owner_user_id=owner,
        purpose="avatar",
        data=b"img",
        idempotency_key=key,
    )


async def approve(media: Any, view: Any, key: str) -> Any:
    return await media.ugc.moderate.approve(
        view.asset_id, moderator_id=MODERATOR, reason=REASON, idempotency_key=key
    )


async def reject(media: Any, view: Any, key: str) -> Any:
    return await media.ugc.moderate.reject(
        view.asset_id,
        moderator_id=MODERATOR,
        reason_code="other",
        reason=REASON,
        idempotency_key=key,
    )


def objects(media: Any) -> list[str]:
    return sorted(media.storage.keys(StorageArea.ASSETS))


# ---------------------------------------------------------------- домен: замена
def test_replace_moves_approved_to_rejected_with_service_code_and_no_event() -> None:
    image = UgcImage.submit(
        uuid7(),
        owner_user_id=OWNER,
        purpose="avatar",
        pending_key=pending_key("a" * 32),
        width=512,
        height=512,
        size_bytes=1,
        sha256="0" * 64,
        now=NOW,
    )
    image.approve(moderator_id=MODERATOR, new_public_key=public_key("b" * 32), now=NOW)
    image.pull_events()

    old_key = image.replace(NOW + timedelta(hours=1))

    assert old_key == public_key("b" * 32)
    assert (image.status, image.reason_code, image.public_key) == (
        UgcStatus.REJECTED,
        REPLACED_REASON,
        None,
    )
    assert image.version == 2  # версия растёт, как при снятии
    assert image.pull_events() == []  # «отклонено» пользователю не шлём


def test_replace_is_only_for_approved() -> None:
    image = UgcImage.submit(
        uuid7(),
        owner_user_id=OWNER,
        purpose="avatar",
        pending_key=pending_key("a" * 32),
        width=1,
        height=1,
        size_bytes=1,
        sha256="0" * 64,
        now=NOW,
    )
    with pytest.raises(UgcNotPendingError):
        image.replace(NOW)


def test_replaced_is_not_a_moderator_reason(media: Any) -> None:
    assert REPLACED_REASON not in {r.code for r in media.ugc_catalog.reasons}


# ---------------------------------------------------------------- замена в use-case
async def test_approving_new_avatar_replaces_previous_approved_silently(media: Any) -> None:
    first = await submit(media, "idem-0000000001")
    await approve(media, first, "mod-0000000001")
    first_public = objects(media)
    assert len(first_public) == 1 and first_public[0].startswith("u/")
    second = await submit(media, "idem-0000000002")

    await approve(media, second, "mod-0000000002")

    a = media.db.ugc[first.asset_id]
    assert (a.status, a.reason_code, a.version) == (UgcStatus.REJECTED, REPLACED_REASON, 2)
    assert media.db.ugc[second.asset_id].status is UgcStatus.APPROVED
    # события для A нет: только два approve (A v1 и B v1)
    events = [e for e in media.db.events if isinstance(e, AssetModerated)]
    assert [(e.asset_id, e.status, e.version) for e in events] == [
        (first.asset_id, "approved", 1),
        (second.asset_id, "approved", 1),
    ]
    remaining = objects(media)
    assert len(remaining) == 1 and remaining[0] not in first_public  # объект A удалён


async def test_replacement_object_is_queued_until_deleted(media: Any) -> None:
    first = await submit(media, "idem-0000000001")
    await approve(media, first, "mod-0000000001")
    [first_public] = objects(media)
    second = await submit(media, "idem-0000000002")
    media.storage.fail_delete = True

    await approve(media, second, "mod-0000000002")

    assert first_public in media.db.purge  # ключ записан в той же транзакции
    assert (StorageArea.ASSETS, first_public) in media.storage.objects  # S3 не удалил
    media.storage.fail_delete = False
    assert await media.ugc.purger.sweep() >= 1
    assert (StorageArea.ASSETS, first_public) not in media.storage.objects
    assert first_public not in media.db.purge


async def test_other_owner_is_not_replaced(media: Any) -> None:
    mine = await submit(media, "idem-0000000001")
    await approve(media, mine, "mod-0000000001")
    other_owner = await submit(media, "idem-0000000002", owner=uuid7())
    await approve(media, other_owner, "mod-0000000002")
    assert media.db.ugc[mine.asset_id].status is UgcStatus.APPROVED


# ---------------------------------------------------------------- гарантированное удаление
async def test_failed_delete_keeps_key_in_purge_table_and_job_removes_it_later(
    media: Any,
) -> None:
    view = await submit(media, "idem-0000000001")
    [pending] = objects(media)
    media.storage.fail_delete = True

    await reject(media, view, "mod-0000000001")  # решение не откатывается из-за S3

    assert media.db.ugc[view.asset_id].status is UgcStatus.REJECTED
    assert media.db.purge.keys() == {pending}
    assert objects(media) == [pending]

    media.storage.fail_delete = False
    assert await media.ugc.purger.sweep() == 1
    assert objects(media) == [] and media.db.purge == {}
    assert await media.ugc.purger.sweep() == 0  # повтор ничего не делает


async def test_sweep_keeps_row_while_storage_still_fails(media: Any) -> None:
    view = await submit(media, "idem-0000000001")
    media.storage.fail_delete = True
    await reject(media, view, "mod-0000000001")
    assert await media.ugc.purger.sweep() == 0
    assert len(media.db.purge) == 1  # будет повтор на следующем запуске


async def test_fast_path_removes_row_when_delete_succeeds(media: Any) -> None:
    view = await submit(media, "idem-0000000001")
    await reject(media, view, "mod-0000000001")
    assert objects(media) == [] and media.db.purge == {}


async def test_supersede_and_approve_queue_pending_keys(media: Any) -> None:
    first = await submit(media, "idem-0000000001")
    media.storage.fail_delete = True
    second = await submit(media, "idem-0000000002")  # снимает первую pending
    assert len(media.db.purge) == 1
    media.storage.fail_delete = False
    await media.ugc.purger.sweep()
    assert media.db.ugc[first.asset_id].reason_code == "superseded"
    assert len(objects(media)) == 1 and media.db.purge == {}
    assert second.asset_id in media.db.ugc


# ---------------------------------------------------------------- осиротевшие p/
async def test_orphan_sweep_deletes_old_unreferenced_pending_objects_only(media: Any) -> None:
    live = await submit(media, "idem-0000000001")
    [live_key] = objects(media)
    old_orphan, young_orphan = pending_key("c" * 32), pending_key("d" * 32)
    for key, age_h in ((old_orphan, 48), (young_orphan, 1)):
        await media.storage.put(StorageArea.ASSETS, key, b"x", "image/webp")
        media.storage.modified[key] = media.clock.now() - timedelta(hours=age_h)
    media.storage.modified[live_key] = media.clock.now() - timedelta(hours=72)  # старый, но живой
    public = public_key("e" * 32)
    await media.storage.put(StorageArea.ASSETS, public, b"x", "image/webp")
    media.storage.modified[public] = media.clock.now() - timedelta(hours=99)

    deleted = await media.ugc.orphans.sweep()

    assert deleted == 1
    assert set(objects(media)) == {live_key, young_orphan, public}
    assert media.db.ugc[live.asset_id].status is UgcStatus.PENDING
