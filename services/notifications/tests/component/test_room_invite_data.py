"""room_invite: ``data.room_code`` из ``social.RoomInviteSent`` доходит до БД, API и realtime."""

from __future__ import annotations

from typing import Any

import pytest
from notifications_service.infrastructure.db.tables import notifications as notifications_table

from gv_core.ids import uuid7
from gv_testkit import assert_realtime_message_matches_schema

pytestmark = pytest.mark.component

Service = Any  # фикстура `notifications` описана в conftest.py


async def test_room_invite_event_stores_data_and_publishes_it(notifications: Service) -> None:
    svc = notifications
    to_user = uuid7()
    await svc.deliver(svc.event("social.RoomInviteSent", to_user_id=str(to_user)))

    [row] = await svc.rows(notifications_table)
    assert (row.type, row.data) == ("room_invite", {"room_code": "K7M2QX"})

    page = (await svc.client.get("/v1/notifications", headers=svc.auth(to_user))).json()
    assert page["items"][0]["data"] == {"room_code": "K7M2QX"}

    [(_, message)] = svc.realtime.published
    assert message["notification"]["data"] == {"room_code": "K7M2QX"}
    assert_realtime_message_matches_schema(message, "notification.created")


async def test_type_without_data_has_empty_data_in_api_and_no_field_in_realtime(
    notifications: Service,
) -> None:
    svc = notifications
    user = uuid7()
    await svc.notify(user, new_level=4)
    page = (await svc.client.get("/v1/notifications", headers=svc.auth(user))).json()
    assert page["items"][0]["data"] == {}
    [(_, message)] = svc.realtime.published
    assert "data" not in message["notification"]
