"""friend_request / friend_accepted: ``data`` из событий social доходит до БД, API и realtime."""

from __future__ import annotations

from typing import Any

import pytest
from notifications_service.infrastructure.db.tables import notifications as notifications_table

from gv_core.ids import uuid7
from gv_testkit import assert_realtime_message_matches_schema

pytestmark = pytest.mark.component

Service = Any  # фикстура `notifications` описана в conftest.py


async def test_friend_request_data_has_author_and_request_id(notifications: Service) -> None:
    svc = notifications
    to_user, author, request_id = uuid7(), uuid7(), uuid7()
    await svc.deliver(
        svc.event(
            "social.FriendRequested",
            to_user_id=str(to_user),
            from_user_id=str(author),
            request_id=str(request_id),
        )
    )
    expected = {"user_id": str(author), "request_id": str(request_id)}

    [row] = await svc.rows(notifications_table)
    assert (row.type, row.data) == ("friend_request", expected)

    page = (await svc.client.get("/v1/notifications", headers=svc.auth(to_user))).json()
    assert page["items"][0]["data"] == expected
    assert all(isinstance(v, str) for v in page["items"][0]["data"].values())

    [(_, message)] = svc.realtime.published
    assert message["notification"]["data"] == expected
    assert_realtime_message_matches_schema(message, "notification.created")


async def test_friend_accepted_data_has_accepter_id(notifications: Service) -> None:
    svc = notifications
    requester, accepter = uuid7(), uuid7()
    await svc.deliver(
        svc.event(
            "social.FriendAdded",
            requester_id=str(requester),
            accepter_id=str(accepter),
        )
    )
    expected = {"user_id": str(accepter)}

    [row] = await svc.rows(notifications_table)
    assert (row.type, row.data) == ("friend_accepted", expected)

    page = (await svc.client.get("/v1/notifications", headers=svc.auth(requester))).json()
    assert page["items"][0]["data"] == expected

    [(_, message)] = svc.realtime.published
    assert message["notification"]["data"] == expected
    assert_realtime_message_matches_schema(message, "notification.created")
