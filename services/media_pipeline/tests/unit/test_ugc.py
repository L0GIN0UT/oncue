"""UGC-изображения (ADR 0048): домен, приём и решения модератора на фейках портов."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from media_pipeline_service.application.ports import StorageArea
from media_pipeline_service.application.queries.ugc import decode_cursor, encode_cursor
from media_pipeline_service.domain.errors import (
    InvalidUgcImageError,
    UgcClientForbiddenError,
    UgcNotFoundError,
    UgcNotPendingError,
    UgcPurposeUnknownError,
    UgcReasonUnknownError,
    UgcTooLargeError,
)
from media_pipeline_service.domain.ugc import (
    AssetModerated,
    UgcImage,
    UgcStatus,
    is_object_key,
    pending_key,
    public_key,
)
from media_pipeline_service.infrastructure.config import load_ugc_catalog

from gv_core.errors import ConflictError, InvalidInputError
from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

OWNER = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000b0001")
OTHER = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000b0002")
MODERATOR = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000a0001")
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
REASON = "проверка"


def _image() -> UgcImage:
    return UgcImage.submit(
        uuid7(),
        owner_user_id=OWNER,
        purpose="avatar",
        pending_key=pending_key("a" * 32),
        width=512,
        height=512,
        size_bytes=100,
        sha256="0" * 64,
        now=NOW,
    )


# ---------------------------------------------------------------- домен
def test_versions_count_decisions_and_events_follow() -> None:
    image = _image()
    assert image.version == 0
    old = image.approve(moderator_id=MODERATOR, new_public_key=public_key("b" * 32), now=NOW)
    assert old == pending_key("a" * 32)
    assert (image.status, image.version, image.pending_key) == (UgcStatus.APPROVED, 1, None)
    removed = image.reject(moderator_id=MODERATOR, reason_code="other", now=NOW)
    assert removed == public_key("b" * 32)  # снятие одобренного удаляет u/
    assert (image.status, image.version, image.public_key) == (UgcStatus.REJECTED, 2, None)
    events = [e for e in image.pull_events() if isinstance(e, AssetModerated)]
    assert [(e.status, e.version, e.public_key, e.reason_code) for e in events] == [
        ("approved", 1, public_key("b" * 32), None),
        ("rejected", 2, None, "other"),
    ]


def test_event_payload_has_no_moderator_or_free_text() -> None:
    image = _image()
    image.reject(moderator_id=MODERATOR, reason_code="copyright", now=NOW)
    payload = image.pull_events()[0].payload()
    assert set(payload) == {
        "asset_id",
        "owner_user_id",
        "purpose",
        "status",
        "reason_code",
        "public_key",
        "decided_at",
        "version",
    }


def test_decisions_from_wrong_status_conflict() -> None:
    image = _image()
    image.reject(moderator_id=MODERATOR, reason_code="other", now=NOW)
    with pytest.raises(UgcNotPendingError):
        image.reject(moderator_id=MODERATOR, reason_code="other", now=NOW)
    with pytest.raises(UgcNotPendingError):
        image.approve(moderator_id=MODERATOR, new_public_key=public_key("c" * 32), now=NOW)


def test_supersede_is_silent_and_keeps_version() -> None:
    image = _image()
    image.supersede(NOW)
    assert (image.status, image.reason_code, image.version) == (UgcStatus.REJECTED, "superseded", 0)
    assert image.pull_events() == []
    with pytest.raises(UgcNotPendingError):
        image.approve(moderator_id=MODERATOR, new_public_key=public_key("c" * 32), now=NOW)


@given(st.lists(st.sampled_from(["approve", "reject"]), max_size=6))
def test_version_is_monotonic_whatever_the_decision_order(actions: list[str]) -> None:
    image = _image()
    seen = 0
    for action in actions:
        try:
            if action == "approve":
                image.approve(moderator_id=MODERATOR, new_public_key=public_key("d" * 32), now=NOW)
            else:
                image.reject(moderator_id=MODERATOR, reason_code="other", now=NOW)
        except UgcNotPendingError:
            assert image.version == seen
            continue
        assert image.version == seen + 1
        seen = image.version


@given(st.text(max_size=40))
def test_object_key_shape_rejects_user_controlled_paths(text: str) -> None:
    assert not is_object_key(text) or text == text.strip()
    assert is_object_key(pending_key("0123456789abcdef" * 2))
    assert not is_object_key("p/../" + "a" * 32 + ".webp")


def test_cursor_roundtrip_and_garbage() -> None:
    ident = uuid7()
    assert decode_cursor(encode_cursor(NOW, ident)) == (NOW, ident)
    with pytest.raises(InvalidInputError):
        decode_cursor("!!")


# ---------------------------------------------------------------- конфиг
def test_builtin_config_limits_and_reason_codes() -> None:
    catalog = load_ugc_catalog()
    avatar = catalog.purpose("avatar")
    assert avatar.max_bytes <= 1_500_000  # client_max_body_size gateway = 2m
    assert avatar.allowed_clients == {"progression"}
    assert [r.code for r in catalog.reasons] == [
        "inappropriate",
        "copyright",
        "low_quality",
        "personal_data",
        "other",
    ]
    with pytest.raises(UgcPurposeUnknownError):
        catalog.purpose("nope")


# ---------------------------------------------------------------- приём
async def _submit(
    media: Any,
    data: bytes = b"img",
    *,
    owner: UUID = OWNER,
    key: str = "idem-0000000001",
    client: str = "progression",
    purpose: str = "avatar",
) -> Any:
    return await media.ugc.submit.execute(
        client_id=client,
        owner_user_id=owner,
        purpose=purpose,
        data=data,
        idempotency_key=key,
    )


async def test_submit_stores_pending_object_under_random_key(media: Any) -> None:
    view = await _submit(media)
    assert (view.status, view.version, view.purpose) == ("pending", 1, "avatar")
    keys = media.storage.keys(StorageArea.ASSETS)
    assert len(keys) == 1 and keys[0].startswith("p/") and is_object_key(keys[0])
    assert media.storage.objects[(StorageArea.ASSETS, keys[0])][1] == "image/webp"
    assert media.db.events == []  # приём событий не публикует


async def test_submit_replay_returns_same_asset_without_reencoding(media: Any) -> None:
    first = await _submit(media)
    second = await _submit(media)
    assert second.asset_id == first.asset_id
    assert media.ugc_encoder.calls == 1
    assert len(media.storage.keys(StorageArea.ASSETS)) == 1
    with pytest.raises(ConflictError) as exc:
        await _submit(media, b"other")
    assert exc.value.code == "idempotency.key_reused"


async def test_idempotency_keys_are_per_owner(media: Any) -> None:
    a = await _submit(media, owner=OWNER)
    b = await _submit(media, owner=OTHER)
    assert a.asset_id != b.asset_id


async def test_submit_checks_purpose_client_and_size(media: Any) -> None:
    with pytest.raises(UgcPurposeUnknownError):
        await _submit(media, purpose="unknown")
    with pytest.raises(UgcClientForbiddenError):
        await _submit(media, client="game")
    limit = media.ugc_catalog.purpose("avatar").max_bytes
    with pytest.raises(UgcTooLargeError):
        await _submit(media, b"x" * (limit + 1))
    assert media.ugc_encoder.calls == 0  # размер и клиент проверены до декодирования


async def test_invalid_image_leaves_no_object_or_row(media: Any) -> None:
    with pytest.raises(InvalidUgcImageError):
        await _submit(media, b"bad file")
    assert media.storage.keys(StorageArea.ASSETS) == [] and media.db.ugc == {}


async def test_commit_failure_deletes_uploaded_object(media: Any) -> None:
    def boom(_: object) -> None:
        raise ConnectionError("обрыв")

    media.fail_commit = boom
    with pytest.raises(ConnectionError):
        await _submit(media)
    assert media.storage.keys(StorageArea.ASSETS) == []


async def test_new_request_silently_supersedes_previous_pending(media: Any) -> None:
    first = await _submit(media, key="idem-0000000001")
    second = await _submit(media, key="idem-0000000002")
    assert media.db.ugc[first.asset_id].status is UgcStatus.REJECTED
    assert media.db.ugc[first.asset_id].reason_code == "superseded"
    assert media.db.ugc[second.asset_id].status is UgcStatus.PENDING
    assert media.db.events == []  # пользователю «отклонено» не нужно
    assert len(media.storage.keys(StorageArea.ASSETS)) == 1  # объект прежней удалён


# ---------------------------------------------------------------- решения
async def test_approve_copies_to_public_key_and_removes_pending(media: Any) -> None:
    view = await _submit(media)
    done = await media.ugc.moderate.approve(
        view.asset_id, moderator_id=MODERATOR, reason=REASON, idempotency_key="mod-0000000001"
    )
    assert (done.status, done.version) == ("approved", 1)
    keys = media.storage.keys(StorageArea.ASSETS)
    assert len(keys) == 1 and keys[0].startswith("u/") and is_object_key(keys[0])
    [event] = media.db.events
    assert isinstance(event, AssetModerated)
    assert (event.status, event.version, event.public_key) == ("approved", 1, keys[0])
    assert [a.action for a in media.db.audit] == ["ugc.approve"]
    assert media.db.audit[0].actor_id == MODERATOR


async def test_approve_replay_and_second_decision(media: Any) -> None:
    view = await _submit(media)
    args = {"moderator_id": MODERATOR, "reason": REASON}
    first = await media.ugc.moderate.approve(view.asset_id, idempotency_key="mod-1", **args)
    again = await media.ugc.moderate.approve(view.asset_id, idempotency_key="mod-1", **args)
    assert again == first and len(media.db.events) == 1
    with pytest.raises(UgcNotPendingError):
        await media.ugc.moderate.approve(view.asset_id, idempotency_key="mod-2", **args)


async def test_approve_unknown_asset_is_404(media: Any) -> None:
    with pytest.raises(UgcNotFoundError):
        await media.ugc.moderate.approve(
            uuid7(), moderator_id=MODERATOR, reason=REASON, idempotency_key="mod-0000000001"
        )


async def test_failed_copy_changes_nothing(media: Any) -> None:
    view = await _submit(media)
    media.storage.fail_copy = True
    with pytest.raises(ConnectionError):
        await media.ugc.moderate.approve(
            view.asset_id, moderator_id=MODERATOR, reason=REASON, idempotency_key="mod-1"
        )
    assert media.db.ugc[view.asset_id].status is UgcStatus.PENDING
    assert media.db.events == []
    assert [k[:2] for k in media.storage.keys(StorageArea.ASSETS)] == ["p/"]


async def test_failed_commit_removes_the_public_copy(media: Any) -> None:
    view = await _submit(media)

    def boom(_: object) -> None:
        raise ConnectionError("обрыв")

    media.fail_commit = boom
    with pytest.raises(ConnectionError):
        await media.ugc.moderate.approve(
            view.asset_id, moderator_id=MODERATOR, reason=REASON, idempotency_key="mod-1"
        )
    # u/ не осталось: неодобренное не должно стать публичным
    assert [k[:2] for k in media.storage.keys(StorageArea.ASSETS)] == ["p/"]


async def test_reject_validates_reason_code_and_deletes_object(media: Any) -> None:
    view = await _submit(media)
    with pytest.raises(UgcReasonUnknownError):
        await media.ugc.moderate.reject(
            view.asset_id,
            moderator_id=MODERATOR,
            reason_code="superseded",  # служебный код в списке причин модератора не нужен
            reason=REASON,
            idempotency_key="mod-1",
        )
    done = await media.ugc.moderate.reject(
        view.asset_id,
        moderator_id=MODERATOR,
        reason_code="copyright",
        reason=REASON,
        idempotency_key="mod-2",
    )
    assert (done.status, done.reason_code, done.version) == ("rejected", "copyright", 1)
    assert media.storage.keys(StorageArea.ASSETS) == []
    [event] = media.db.events
    assert isinstance(event, AssetModerated) and event.public_key is None


async def test_takedown_of_approved_bumps_version_and_removes_public_object(
    media: Any,
) -> None:
    view = await _submit(media)
    await media.ugc.moderate.approve(
        view.asset_id, moderator_id=MODERATOR, reason=REASON, idempotency_key="mod-1"
    )
    done = await media.ugc.moderate.reject(
        view.asset_id,
        moderator_id=MODERATOR,
        reason_code="inappropriate",
        reason=REASON,
        idempotency_key="mod-2",
    )
    assert (done.status, done.version) == ("rejected", 2)
    assert media.storage.keys(StorageArea.ASSETS) == []
    assert [e.version for e in media.db.events if isinstance(e, AssetModerated)] == [1, 2]
    with pytest.raises(UgcNotPendingError):  # повторное снятие
        await media.ugc.moderate.reject(
            view.asset_id,
            moderator_id=MODERATOR,
            reason_code="other",
            reason=REASON,
            idempotency_key="mod-3",
        )


async def test_preview_signs_pending_only_and_audits(media: Any) -> None:
    view = await _submit(media)
    preview = await media.ugc.preview.execute(view.asset_id, moderator_id=MODERATOR)
    assert "/p/" in preview.url and "expires=" in preview.url
    assert [a.action for a in media.db.audit] == ["ugc.preview"]
    await media.ugc.moderate.reject(
        view.asset_id,
        moderator_id=MODERATOR,
        reason_code="other",
        reason=REASON,
        idempotency_key="mod-1",
    )
    with pytest.raises(UgcNotFoundError):
        await media.ugc.preview.execute(view.asset_id, moderator_id=MODERATOR)


async def test_queue_is_oldest_first_with_cursor(media: Any) -> None:
    ids = []
    for i in range(3):
        media.clock.advance(seconds=1)
        ids.append((await _submit(media, owner=uuid7(), key=f"idem-00000000{i}")).asset_id)
    page = await media.ugc.queue.execute(
        status=UgcStatus.PENDING, purpose=None, cursor=None, limit=2
    )
    assert [v.asset_id for v in page.items] == ids[:2] and page.next_cursor
    rest = await media.ugc.queue.execute(
        status=UgcStatus.PENDING, purpose="avatar", cursor=page.next_cursor, limit=2
    )
    assert [v.asset_id for v in rest.items] == ids[2:] and rest.next_cursor is None


# ---------------------------------------------------------------- решение модерации social
async def _approved(media: Any) -> Any:
    view = await _submit(media)
    await media.ugc.moderate.approve(
        view.asset_id, moderator_id=MODERATOR, reason=REASON, idempotency_key="mod-approve"
    )
    return view


def _removal(media: Any) -> Any:
    from media_pipeline_service.application.commands.ugc import RemoveModeratedUgcImage

    return RemoveModeratedUgcImage(media.ugc.moderate, reason_code="inappropriate")


async def test_social_removal_takes_approved_avatar_down_idempotently(media: Any) -> None:
    view = await _approved(media)
    case = uuid7()
    removal = _removal(media)
    assert await removal.execute(view.asset_id, moderator_id=MODERATOR, case_id=case) is True
    image = media.db.ugc[view.asset_id]
    assert (image.status, image.reason_code) == (UgcStatus.REJECTED, "inappropriate")
    moderated = [e for e in media.db.events if isinstance(e, AssetModerated)]
    assert [e.status for e in moderated] == ["approved", "rejected"]
    # Повтор того же решения и другой кейс по уже снятому - без нового события.
    await removal.execute(view.asset_id, moderator_id=MODERATOR, case_id=case)  # повтор по ключу
    assert await removal.execute(view.asset_id, moderator_id=MODERATOR, case_id=uuid7()) is False
    assert len([e for e in media.db.events if isinstance(e, AssetModerated)]) == 2


async def test_social_removal_of_unknown_asset_is_noop(media: Any) -> None:
    assert await _removal(media).execute(uuid7(), moderator_id=MODERATOR, case_id=uuid7()) is False
    assert media.db.events == []
