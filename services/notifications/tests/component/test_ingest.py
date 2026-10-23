"""События -> уведомления через настоящий EventProcessor (inbox, транзакция)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest
from notifications_service.infrastructure.db.tables import notification_preferences
from notifications_service.infrastructure.db.tables import notifications as notifications_table
from notifications_service.main import DEFINITION

from gv_core.ids import uuid7
from gv_messaging import Outcome
from gv_testkit import assert_realtime_message_matches_schema
from gv_testkit.contracts import contracts_dir

pytestmark = pytest.mark.component

N = notifications_table.c
Service = Any  # фикстура `notifications` описана в conftest.py


async def test_level_up_event_becomes_notification(notifications: Service) -> None:
    user = uuid7()
    await notifications.notify(user, new_level=7)
    [row] = await notifications.rows(notifications_table)
    assert row.user_id == user
    assert (row.type, row.title) == ("level_up", "Новый уровень")
    assert "7" in row.body
    assert row.read_at is None


async def test_each_5b_event_maps_to_its_type(notifications: Service) -> None:
    user = str(uuid7())
    svc = notifications
    events = [
        svc.event("progression.BadgeUnlocked", user_id=user),
        svc.event("progression.QuestCompleted", user_id=user),
        svc.event("progression.CurrencyCredited", user_id=user, reason="weekly_top", op_id="w1"),
        svc.event("progression.CurrencyCredited", user_id=user, reason="season", op_id="s1"),
        svc.event(
            "media_pipeline.AssetModerated", owner_user_id=user, status="approved", reason_code=None
        ),
        svc.event(
            "media_pipeline.AssetModerated", owner_user_id=user, status="rejected", version=3
        ),
    ]
    for envelope in events:
        assert await svc.deliver(envelope) is Outcome.PROCESSED
    types = sorted(r.type for r in await svc.rows(notifications_table))
    assert types == sorted(
        [
            "badge_unlocked",
            "quest_completed",
            "weekly_top_reward",
            "season_reward",
            "avatar_approved",
            "avatar_rejected",
        ]
    )


async def test_event_with_no_matching_type_is_skipped_without_error(notifications: Service) -> None:
    """CurrencyCredited с другой причиной (duel) и чужое событие - не уведомление и не DLQ."""
    svc = notifications
    assert await svc.deliver(svc.event("progression.CurrencyCredited")) is Outcome.PROCESSED
    assert await svc.deliver(svc.event("progression.WheelSpun")) is Outcome.SKIPPED
    assert await svc.count(notifications_table) == 0
    assert svc.dead_letters.letters == []


async def test_other_asset_purpose_is_ignored(notifications: Service) -> None:
    svc = notifications
    await svc.deliver(svc.event("media_pipeline.AssetModerated", purpose="collection_cover"))
    assert await svc.count(notifications_table) == 0


async def test_same_event_id_twice_gives_one_notification(notifications: Service) -> None:
    svc = notifications
    envelope = svc.event("progression.ProgressionLevelChanged", new_level=3)
    assert await svc.deliver(envelope) is Outcome.PROCESSED
    assert await svc.deliver(envelope) is Outcome.DUPLICATE
    assert await svc.count(notifications_table) == 1


async def test_repeat_with_new_event_id_does_not_create_second_notification(
    notifications: Service,
) -> None:
    """Дедупликация по предмету события (dedup_key), а не по event_id."""
    svc = notifications
    first = svc.event("progression.ProgressionLevelChanged", new_level=3)
    again = first.model_copy(update={"event_id": uuid7()})
    assert await svc.deliver(first) is Outcome.PROCESSED
    assert await svc.deliver(again) is Outcome.PROCESSED
    assert await svc.count(notifications_table) == 1
    assert len(svc.realtime.published) == 1  # повтор в канал не уходит


async def test_other_level_is_another_notification(notifications: Service) -> None:
    user = uuid7()
    await notifications.notify(user, new_level=3)
    await notifications.notify(user, new_level=4)
    assert await notifications.count(notifications_table) == 2


async def test_avatar_rejected_goes_to_owner_with_reason_code(notifications: Service) -> None:
    owner = uuid7()
    svc = notifications
    await svc.deliver(
        svc.event(
            "media_pipeline.AssetModerated", owner_user_id=str(owner), reason_code="copyright"
        )
    )
    [row] = await svc.rows(notifications_table)
    assert row.user_id == owner
    assert row.type == "avatar_rejected"
    assert row.body == "Ваша аватарка отклонена: нарушает чужие авторские права."


async def test_broken_payload_goes_to_dlq_as_permanent(notifications: Service) -> None:
    svc = notifications
    envelope = svc.event("progression.ProgressionLevelChanged", user_id="not-a-uuid")
    assert await svc.deliver(envelope) is Outcome.DEAD
    assert svc.dead_letters.letters[-1]["reason"] == "permanent"
    assert await svc.count(notifications_table) == 0


async def test_missing_field_goes_to_dlq(notifications: Service) -> None:
    svc = notifications
    envelope = svc.event("progression.ProgressionLevelChanged")
    payload = dict(envelope.payload)
    del payload["new_level"]
    assert await svc.deliver(envelope.model_copy(update={"payload": payload})) is Outcome.DEAD
    assert svc.dead_letters.letters[-1]["reason"] == "permanent"


async def test_muted_type_creates_no_notification(notifications: Service) -> None:
    svc = notifications
    user = uuid7()
    headers = svc.auth(user)
    response = await svc.client.put(
        "/v1/notification-settings/level_up",
        json={"in_app": False, "email": False},
        headers=headers,
    )
    assert response.status_code == 200
    await svc.notify(user, new_level=5)
    assert await svc.count(notifications_table) == 0
    assert svc.realtime.published == []
    # другой тип у того же игрока работает
    await svc.deliver(svc.event("progression.BadgeUnlocked", user_id=str(user)))
    assert await svc.count(notifications_table) == 1


async def test_muting_does_not_affect_other_users(notifications: Service) -> None:
    svc = notifications
    muted, other = uuid7(), uuid7()
    await svc.client.put(
        "/v1/notification-settings/level_up",
        json={"in_app": False, "email": False},
        headers=svc.auth(muted),
    )
    await svc.notify(other, new_level=5)
    assert await svc.count(notifications_table) == 1


async def test_non_mutable_type_ignores_preferences_row(notifications: Service) -> None:
    """Даже если строка настройки попала в БД, avatar_* (user_mutable=false) доставляется."""
    svc = notifications
    owner = uuid7()
    assert svc.infra.sessions is not None
    async with svc.infra.sessions() as session, session.begin():
        await session.execute(
            notification_preferences.insert().values(
                user_id=owner,
                type="avatar_approved",
                in_app=False,
                email=False,
                updated_at=svc.clock.now(),
            )
        )
    await svc.deliver(
        svc.event(
            "media_pipeline.AssetModerated",
            owner_user_id=str(owner),
            status="approved",
            reason_code=None,
        )
    )
    assert await svc.count(notifications_table, N.user_id == owner) == 1


async def test_realtime_message_goes_to_personal_channel_after_commit(
    notifications: Service,
) -> None:
    user = uuid7()
    await notifications.notify(user, new_level=9)
    [(channel, message)] = notifications.realtime.published
    assert channel == f"personal:#{user}"
    assert message["type"] == "notification.created"
    assert message["notification"]["type"] == "level_up"
    assert UUID(message["notification"]["id"])
    assert_realtime_message_matches_schema(message, "notification.created")


def test_realtime_example_matches_schema() -> None:
    example = json.loads(
        (contracts_dir() / "realtime" / "examples" / "notification.created.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert_realtime_message_matches_schema(example, "notification.created")


async def test_realtime_is_not_published_when_transaction_rolls_back(
    notifications: Service,
) -> None:
    svc = notifications
    user = uuid7()
    envelope = svc.event("progression.ProgressionLevelChanged", user_id=str(user), new_level=2)
    handler = DEFINITION.build_event_handlers(svc.infra).get(envelope.event_type)  # type: ignore[misc]  # задан в DEFINITION
    assert handler is not None
    assert svc.infra.sessions is not None
    async with svc.infra.sessions() as session:
        transaction = await session.begin()
        await handler(envelope, session)
        await transaction.rollback()
    await svc.container.dispatcher.drain()
    assert svc.realtime.published == []
    assert await svc.count(notifications_table) == 0
