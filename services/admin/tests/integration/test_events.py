"""Потребитель identity.user.v1: синхронизация ролей по version, inbox, DLQ."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from admin_service.core.events import UserEventsProcessor, handle_envelope
from admin_service.core.models import AdminUser, ProcessedEvent
from admin_service.core.permissions import PermissionSync

from gv_core.ids import uuid7
from gv_messaging.consumer import Outcome
from gv_messaging.envelope import EventEnvelope
from gv_messaging.retry import RetryPolicy
from gv_testkit.contracts import assert_event_matches_schema, load_example

pytestmark = pytest.mark.integration

USER = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000001")  # user_id из примеров contracts/


def _mirror(version: int = 1) -> AdminUser:
    user = AdminUser.objects.create(
        username=f"identity-{USER}", identity_user_id=USER, identity_version=version
    )
    PermissionSync.apply(user, roles=["admin"], perms=["admin.access", "roles.assign"])
    return user


def _event(name: str, **payload: Any) -> EventEnvelope:
    example = load_example(f"identity.{name}")
    envelope = example.model_copy(
        update={"event_id": uuid7(), "payload": {**example.payload, **payload}}
    )
    assert_event_matches_schema(envelope)  # тесты не уходят от контракта
    return envelope


@pytest.mark.django_db
class TestHandleEnvelope:
    def test_newer_roles_are_applied(self) -> None:
        _mirror(version=1)
        assert handle_envelope(_event("UserRolesChanged", version=3)) is Outcome.PROCESSED
        user = AdminUser.objects.get(identity_user_id=USER)
        assert user.identity_version == 3
        assert user.identity_roles == ["moderator", "user"]
        assert not user.is_superuser and user.is_staff
        assert not user.has_perm("identity.roles.assign")
        assert user.has_perm("identity.catalog.block_item")

    def test_stale_version_is_ignored(self) -> None:
        _mirror(version=5)
        assert handle_envelope(_event("UserRolesChanged", version=3)) is Outcome.PROCESSED
        user = AdminUser.objects.get(identity_user_id=USER)
        assert user.identity_version == 5
        assert user.is_superuser  # права не откатились

    def test_same_version_is_ignored(self) -> None:
        _mirror(version=3)
        handle_envelope(_event("UserRolesChanged", version=3, perms=[]))
        assert AdminUser.objects.get(identity_user_id=USER).is_staff

    def test_duplicate_delivery_is_skipped(self) -> None:
        _mirror(version=1)
        event = _event("UserRolesChanged", version=2)
        assert handle_envelope(event) is Outcome.PROCESSED
        AdminUser.objects.filter(identity_user_id=USER).update(identity_version=0)
        assert handle_envelope(event) is Outcome.DUPLICATE
        assert ProcessedEvent.objects.count() == 1

    def test_unknown_user_is_not_created(self) -> None:
        assert handle_envelope(_event("UserRolesChanged", version=2)) is Outcome.PROCESSED
        assert not AdminUser.objects.exists()

    def test_user_deleted_revokes_access(self) -> None:
        _mirror(version=3)
        assert handle_envelope(_event("UserDeleted", version=4)) is Outcome.PROCESSED
        user = AdminUser.objects.get(identity_user_id=USER)
        assert not user.is_active and not user.is_staff and user.identity_perms == []
        assert user.groups.count() == 0

    def test_stale_user_deleted_is_ignored(self) -> None:
        _mirror(version=5)
        handle_envelope(_event("UserDeleted", version=4))
        assert AdminUser.objects.get(identity_user_id=USER).is_active

    def test_other_events_are_skipped(self) -> None:
        assert handle_envelope(_event("UserRegistered")) is Outcome.SKIPPED
        assert not ProcessedEvent.objects.exists()


class _Dlq:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, raw: bytes, *, reason: str, error: str, source: str) -> None:
        self.sent.append(reason)


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
async def close_worker_connections() -> Any:
    """Закрыть соединение потока sync_to_async: иначе тестовую БД нельзя удалить."""
    from asgiref.sync import sync_to_async
    from django.db import connections

    yield
    await sync_to_async(connections.close_all)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("close_worker_connections")
class TestProcessor:
    async def test_processes_raw_message(self) -> None:
        from asgiref.sync import sync_to_async

        await sync_to_async(_mirror)(1)
        processor = UserEventsProcessor(dead_letters=_Dlq(), sleep=_no_sleep)
        raw = _event("UserRolesChanged", version=2).to_bytes()
        assert await processor.process(raw, source="t:0:1") is Outcome.PROCESSED
        user = await AdminUser.objects.aget(identity_user_id=USER)
        assert user.identity_version == 2

    async def test_malformed_goes_to_dlq(self) -> None:
        dlq = _Dlq()
        processor = UserEventsProcessor(dead_letters=dlq, sleep=_no_sleep)
        assert await processor.process(b"{not json", source="t:0:2") is Outcome.DEAD
        assert dlq.sent == ["malformed"]

    async def test_payload_out_of_contract_goes_to_dlq_without_retries(self) -> None:
        dlq = _Dlq()
        processor = UserEventsProcessor(
            dead_letters=dlq, sleep=_no_sleep, retry=RetryPolicy(max_attempts=5)
        )
        event = load_example("identity.UserRolesChanged").model_copy(
            update={"event_id": uuid7(), "payload": {"user_id": "x"}}
        )
        assert await processor.process(event.to_bytes(), source="t:0:3") is Outcome.DEAD
        assert dlq.sent == ["permanent"]
        assert not await ProcessedEvent.objects.aexists()  # inbox откатился вместе с ошибкой
