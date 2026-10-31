"""Санкции игрока в identity через событие social: токен, бан аккаунта, отзыв сессий (ADR 0052)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import jwt
import pytest
from identity_service.infrastructure.db.tables import TABLES, user_sanctions
from identity_service.main import DEFINITION
from sqlalchemy import select, text

from gv_auth import ACCESS_COOKIE, Sanction
from gv_core.ids import uuid7
from gv_messaging import EventEnvelope, EventProcessor, Inbox, Outcome
from gv_messaging.retry import RetryPolicy
from gv_testkit import FakeDeadLetterSink, assert_problem, load_example

pytestmark = pytest.mark.component

EVENT = "social.UserSanctionsChanged"
MASK = int(Sanction.CHAT_MUTE | Sanction.COMMENTS | Sanction.ONLINE)


class Consumer:
    def __init__(self, service: Any) -> None:
        self.service = service
        self.dead = FakeDeadLetterSink()
        assert DEFINITION.build_event_handlers is not None

        async def no_sleep(_: float) -> None:
            return None

        self.processor = EventProcessor(
            group="identity.consumer",
            registry=DEFINITION.build_event_handlers(service.infra),
            session_factory=service.infra.sessions,
            inbox=Inbox(TABLES.inbox),
            dead_letters=self.dead,
            clock=service.infra.clock,
            retry=RetryPolicy(max_attempts=2, base_delay=0.0, max_delay=0.0),
            sleep=no_sleep,
        )

    def event(self, user_id: UUID, version: int, **payload: Any) -> EventEnvelope:
        envelope = load_example(EVENT)
        # Сроки из примера уходят в прошлое: identity считает маску на «сейчас» и отбрасывает
        # истёкшие санкции, поэтому сдвигаем их от часов сервиса.
        expiry = (self.service.infra.clock.now() + timedelta(days=7)).isoformat()
        active = [
            {**entry, "expires_at": expiry if entry["expires_at"] else None}
            for entry in envelope.payload["active"]
        ]
        base = {
            **envelope.payload,
            "user_id": str(user_id),
            "version": version,
            "active": active,
            "next_expiry_at": expiry,
        }
        return envelope.model_copy(update={"payload": {**base, **payload}})

    async def deliver(self, envelope: EventEnvelope) -> Outcome:
        return await self.processor.process(envelope.to_bytes(), source="test")

    def lift(self, user_id: UUID, version: int) -> EventEnvelope:
        return self.event(
            user_id,
            version,
            active=[],
            mask=0,
            account_banned=False,
            next_expiry_at=None,
        )

    def ban(self, user_id: UUID, version: int) -> EventEnvelope:
        return self.event(
            user_id,
            version,
            active=[{"kind": "account", "expires_at": None, "sanction_id": str(uuid7())}],
            mask=0,
            account_banned=True,
            next_expiry_at=None,
        )


async def _user_id(service: Any, nickname: str = "neo_2026") -> UUID:
    async with service.infra.sessions() as session:
        found = await session.scalar(
            text("SELECT id FROM users WHERE nickname = :n"), {"n": nickname}
        )
    assert isinstance(found, UUID)
    return found


def _claims(service: Any) -> dict[str, Any]:
    token = service.client.cookies.get(ACCESS_COOKIE)
    return jwt.decode(token, options={"verify_signature": False})  # type: ignore[no-any-return]


async def test_event_puts_mask_in_new_access_token(service: Any) -> None:
    await service.register()
    user_id = await _user_id(service)
    consumer = Consumer(service)
    assert await consumer.deliver(consumer.event(user_id, 1)) is Outcome.PROCESSED
    assert (await service.login()).status_code == 200
    assert _claims(service)["sanctions"] == MASK
    async with service.infra.sessions() as session:
        row = (await session.execute(select(user_sanctions))).one()
    assert (row.user_id, row.version, row.mask, row.account_banned) == (user_id, 1, MASK, False)


async def test_duplicate_and_older_events_do_not_regress(service: Any) -> None:
    await service.register()
    user_id = await _user_id(service)
    consumer = Consumer(service)
    first = consumer.event(user_id, 5)
    assert await consumer.deliver(first) is Outcome.PROCESSED
    assert await consumer.deliver(first) is Outcome.DUPLICATE
    assert await consumer.deliver(consumer.lift(user_id, 4)) is Outcome.PROCESSED  # устарело
    await service.login()
    assert _claims(service)["sanctions"] == MASK


async def test_account_ban_revokes_sessions_and_blocks_login_and_refresh(service: Any) -> None:
    await service.register()
    await service.login()
    access = service.client.cookies.get(ACCESS_COOKIE)
    user_id = await _user_id(service)
    consumer = Consumer(service)
    assert await consumer.deliver(consumer.ban(user_id, 1)) is Outcome.PROCESSED

    # Выданный токен перестал приниматься сразу (sid в denylist), refresh закрыт.
    me = await service.client.get("/v1/me", headers={"Authorization": f"Bearer {access}"})
    assert_problem(me, 401, "auth.session_revoked")
    refresh = await service.client.post("/v1/auth/refresh", headers=service.csrf())
    assert refresh.status_code in (401, 403)
    service.forget_session()
    banned = await service.login()
    body = assert_problem(banned, 403, "auth.account_banned")
    assert "reason" not in str(body).lower() or body.get("reason") is None
    # Чужой пароль не раскрывает бан.
    wrong = await service.login(password="wrong password here")
    assert wrong.status_code == 401


async def test_ban_lifted_allows_new_session_with_clean_token(service: Any) -> None:
    await service.register()
    user_id = await _user_id(service)
    consumer = Consumer(service)
    await consumer.deliver(consumer.ban(user_id, 1))
    service.forget_session()
    assert_problem(await service.login(), 403, "auth.account_banned")
    await consumer.deliver(consumer.lift(user_id, 2))
    assert (await service.login()).status_code == 200
    assert _claims(service)["sanctions"] == 0


async def test_broken_payload_goes_to_dlq(service: Any) -> None:
    await service.register()
    consumer = Consumer(service)
    broken = consumer.event(await _user_id(service), 1, active=[{"kind": "online"}])
    assert await consumer.deliver(broken) is Outcome.DEAD
    assert [d["reason"] for d in consumer.dead.letters] == ["permanent"]


async def test_guest_is_not_affected(service: Any) -> None:
    await service.register()
    consumer = Consumer(service)
    await consumer.deliver(consumer.ban(await _user_id(service), 1))
    service.forget_session()
    response = await service.client.post("/v1/auth/guest", headers={"Origin": "https://gv.test"})
    assert response.status_code == 204, response.text
    assert service.client.cookies.get(ACCESS_COOKIE)
