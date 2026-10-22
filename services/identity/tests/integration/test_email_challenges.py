"""Коды из писем и доверенные устройства на реальном Postgres (ADR 0041)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from identity_service.application.ports import ChallengePurpose, EmailChallenge, TrustedDevice
from identity_service.domain import DisplayName, EmailAddress, Nickname, User
from identity_service.infrastructure.db.repositories import (
    SqlChallengeAttempts,
    SqlIdentityUnitOfWork,
)
from identity_service.main import DEFINITION
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gv_core import ConflictError
from gv_core.ids import uuid7
from gv_testkit import FakeClock, apply_migrations

pytestmark = pytest.mark.integration

TTL = timedelta(minutes=10)


@pytest.fixture
async def factory(database_url: str) -> AsyncIterator[async_sessionmaker[Any]]:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    engine = create_async_engine(database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _uow(factory: async_sessionmaker[Any], clock: FakeClock) -> SqlIdentityUnitOfWork:
    return SqlIdentityUnitOfWork(factory, producer="identity", clock=clock)


def _challenge(
    clock: FakeClock,
    *,
    subject: str = "subj-1",
    purpose: ChallengePurpose = ChallengePurpose.RESET,
    max_attempts: int = 5,
    user_id: UUID | None = None,
) -> EmailChallenge:
    now = clock.now()
    return EmailChallenge(
        id=uuid7(),
        purpose=purpose,
        subject_hash=subject,
        user_id=user_id,
        code_hash="h" * 64,
        attempts=0,
        max_attempts=max_attempts,
        send_count=1,
        device_hash=None,
        session_id=None,
        created_at=now,
        expires_at=now + TTL,
        last_sent_at=now,
    )


async def _store(
    factory: async_sessionmaker[Any], clock: FakeClock, challenge: EmailChallenge
) -> None:
    async with _uow(factory, clock) as uow:
        await uow.email_challenges.add(challenge)
        await uow.commit()


async def _user(factory: async_sessionmaker[Any], clock: FakeClock) -> UUID:
    user = User.register(
        uuid7(),
        nickname=Nickname.parse("Neo_2026"),
        email=EmailAddress.parse("neo@example.com"),
        display_name=DisplayName.parse("Neo"),
        locale="ru",
        roles=frozenset({"user"}),
        now=clock.now(),
    )
    async with _uow(factory, clock) as uow:
        await uow.users.add(user)
        await uow.commit()
    return user.id


# ----------------------------------------------------------------------------- email_challenges


async def test_roundtrip(factory: async_sessionmaker[Any], fake_clock: FakeClock) -> None:
    user_id = await _user(factory, fake_clock)
    ch = _challenge(fake_clock, user_id=user_id)
    await _store(factory, fake_clock, ch)
    async with _uow(factory, fake_clock) as uow:
        assert await uow.email_challenges.get(ch.id) == ch
        assert await uow.email_challenges.get(uuid7()) is None


async def test_single_active_code_per_subject_and_purpose(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    first = _challenge(fake_clock)
    await _store(factory, fake_clock, first)

    async with _uow(factory, fake_clock) as uow:
        with pytest.raises(ConflictError) as info:
            await uow.email_challenges.add(_challenge(fake_clock))
        assert info.value.code == "auth.code_active_exists"
        # другое назначение и другой субъект — можно; транзакция жива после конфликта
        await uow.email_challenges.add(_challenge(fake_clock, purpose=ChallengePurpose.REGISTER))
        await uow.email_challenges.add(_challenge(fake_clock, subject="subj-2"))
        # после отмены прежнего — можно
        assert (
            await uow.email_challenges.cancel_active(
                "subj-1", ChallengePurpose.RESET, fake_clock.now()
            )
            == 1
        )
        await uow.email_challenges.add(_challenge(fake_clock))
        await uow.commit()


async def test_expired_unconsumed_code_still_blocks_until_cancelled(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    await _store(factory, fake_clock, _challenge(fake_clock))
    fake_clock.advance(TTL + timedelta(seconds=1))
    async with _uow(factory, fake_clock) as uow:
        with pytest.raises(ConflictError):
            await uow.email_challenges.add(_challenge(fake_clock))
        assert await uow.email_challenges.cancel_active(
            "subj-1", ChallengePurpose.RESET, fake_clock.now()
        )
        await uow.email_challenges.add(_challenge(fake_clock))


async def test_consume_is_single_shot(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    ch = _challenge(fake_clock)
    await _store(factory, fake_clock, ch)
    async with _uow(factory, fake_clock) as uow:
        assert await uow.email_challenges.consume(ch.id, fake_clock.now()) is True
        assert await uow.email_challenges.consume(ch.id, fake_clock.now()) is False
        await uow.commit()
    async with _uow(factory, fake_clock) as uow:
        stored = await uow.email_challenges.get(ch.id)
        assert stored is not None and stored.consumed_at == fake_clock.now()


async def test_consume_expired_or_cancelled_is_false(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    expired = _challenge(fake_clock, subject="a")
    cancelled = _challenge(fake_clock, subject="b")
    await _store(factory, fake_clock, expired)
    await _store(factory, fake_clock, cancelled)
    async with _uow(factory, fake_clock) as uow:
        await uow.email_challenges.cancel_active("b", ChallengePurpose.RESET, fake_clock.now())
        assert await uow.email_challenges.consume(cancelled.id, fake_clock.now()) is False
        assert await uow.email_challenges.consume(expired.id, expired.expires_at) is False
        assert await uow.email_challenges.consume(
            expired.id, expired.expires_at - timedelta(seconds=1)
        )


async def test_concurrent_consume_succeeds_once(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    ch = _challenge(fake_clock)
    await _store(factory, fake_clock, ch)

    async def attempt() -> bool:
        async with _uow(factory, fake_clock) as uow:
            ok = await uow.email_challenges.consume(ch.id, fake_clock.now())
            await uow.commit()
            return ok

    results = await asyncio.gather(*(attempt() for _ in range(8)))
    assert results.count(True) == 1


async def test_replace_code(factory: async_sessionmaker[Any], fake_clock: FakeClock) -> None:
    ch = _challenge(fake_clock)
    await _store(factory, fake_clock, ch)
    attempts = SqlChallengeAttempts(factory)
    assert await attempts.spend(ch.id, fake_clock.now())
    fake_clock.advance(timedelta(seconds=61))
    new_expiry = fake_clock.now() + TTL
    async with _uow(factory, fake_clock) as uow:
        ok = await uow.email_challenges.replace_code(
            ch.id, code_hash="n" * 64, expires_at=new_expiry, now=fake_clock.now()
        )
        assert ok
        stored = await uow.email_challenges.get(ch.id)
        assert stored is not None
        assert (stored.code_hash, stored.send_count, stored.attempts) == ("n" * 64, 2, 0)
        assert stored.expires_at == new_expiry and stored.last_sent_at == fake_clock.now()
        # погашенный код заменить нельзя
        await uow.email_challenges.consume(ch.id, fake_clock.now())
        assert not await uow.email_challenges.replace_code(
            ch.id, code_hash="x" * 64, expires_at=new_expiry, now=fake_clock.now()
        )
        assert not await uow.email_challenges.replace_code(
            uuid7(), code_hash="x" * 64, expires_at=new_expiry, now=fake_clock.now()
        )


async def test_replace_code_on_expired_is_false(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    ch = _challenge(fake_clock)
    await _store(factory, fake_clock, ch)
    fake_clock.advance(TTL + timedelta(seconds=1))
    async with _uow(factory, fake_clock) as uow:
        assert not await uow.email_challenges.replace_code(
            ch.id,
            code_hash="n" * 64,
            expires_at=fake_clock.now() + TTL,
            now=fake_clock.now(),
        )


# ----------------------------------------------------------------------------- spend


async def test_spend_survives_rollback_of_outer_transaction(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    """Ключевой тест безопасности: откат запроса не возвращает попытку (ADR 0041)."""
    ch = _challenge(fake_clock)
    await _store(factory, fake_clock, ch)
    attempts = SqlChallengeAttempts(factory)

    with pytest.raises(RuntimeError):
        async with _uow(factory, fake_clock) as uow:
            spent = await attempts.spend(ch.id, fake_clock.now())
            assert spent is not None and spent.attempts == 1
            assert await uow.email_challenges.get(ch.id) is not None
            raise RuntimeError("сбой после проверки — внешняя транзакция откатится")

    async with _uow(factory, fake_clock) as uow:
        stored = await uow.email_challenges.get(ch.id)
        assert stored is not None and stored.attempts == 1


async def test_spend_stops_at_max_attempts(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    ch = _challenge(fake_clock, max_attempts=3)
    await _store(factory, fake_clock, ch)
    attempts = SqlChallengeAttempts(factory)
    seen = [(await attempts.spend(ch.id, fake_clock.now())) for _ in range(3)]
    assert [s.attempts for s in seen if s] == [1, 2, 3]
    assert await attempts.spend(ch.id, fake_clock.now()) is None
    async with _uow(factory, fake_clock) as uow:
        stored = await uow.email_challenges.get(ch.id)
        assert stored is not None and stored.attempts == 3


async def test_spend_inactive_codes_return_none(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    consumed, expired = _challenge(fake_clock, subject="a"), _challenge(fake_clock, subject="b")
    await _store(factory, fake_clock, consumed)
    await _store(factory, fake_clock, expired)
    async with _uow(factory, fake_clock) as uow:
        await uow.email_challenges.consume(consumed.id, fake_clock.now())
        await uow.commit()
    attempts = SqlChallengeAttempts(factory)
    assert await attempts.spend(consumed.id, fake_clock.now()) is None
    assert await attempts.spend(expired.id, expired.expires_at) is None
    assert await attempts.spend(uuid7(), fake_clock.now()) is None


async def test_concurrent_spend_never_exceeds_max_attempts(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    ch = _challenge(fake_clock, max_attempts=5)
    await _store(factory, fake_clock, ch)
    attempts = SqlChallengeAttempts(factory)
    results = await asyncio.gather(*(attempts.spend(ch.id, fake_clock.now()) for _ in range(20)))
    assert sum(r is not None for r in results) == 5
    assert sorted(r.attempts for r in results if r) == [1, 2, 3, 4, 5]


# ----------------------------------------------------------------------------- failures_since


async def test_failures_since_counts_spent_attempts_across_resend_and_cancel(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    attempts = SqlChallengeAttempts(factory)
    old = _challenge(fake_clock, subject="s")
    await _store(factory, fake_clock, old)
    for _ in range(2):
        await attempts.spend(old.id, fake_clock.now())
    long_ago = fake_clock.now() - timedelta(days=1)
    async with _uow(factory, fake_clock) as uow:
        # resend обнуляет attempts, но не дневной счётчик неудач
        fake_clock.advance(timedelta(seconds=61))
        assert await uow.email_challenges.replace_code(
            old.id, code_hash="n" * 64, expires_at=fake_clock.now() + TTL, now=fake_clock.now()
        )
        await uow.commit()
    await attempts.spend(old.id, fake_clock.now())
    since = fake_clock.now() - timedelta(hours=1)
    async with _uow(factory, fake_clock) as uow:
        assert await uow.email_challenges.failures_since("s", since) == 3
        # отмена записи не стирает неудачи
        await uow.email_challenges.cancel_active("s", ChallengePurpose.RESET, fake_clock.now())
        assert await uow.email_challenges.failures_since("s", since) == 3
        assert await uow.email_challenges.failures_since("other", since) == 0
        assert await uow.email_challenges.failures_since("s", fake_clock.now() + TTL) == 0
        assert await uow.email_challenges.failures_since("s", long_ago) == 3


async def test_failures_since_does_not_count_the_successful_attempt(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    attempts = SqlChallengeAttempts(factory)
    ch = _challenge(fake_clock, subject="s")
    await _store(factory, fake_clock, ch)
    await attempts.spend(ch.id, fake_clock.now())  # неверный
    await attempts.spend(ch.id, fake_clock.now())  # верный
    async with _uow(factory, fake_clock) as uow:
        assert await uow.email_challenges.consume(ch.id, fake_clock.now())
        assert (
            await uow.email_challenges.failures_since("s", fake_clock.now() - timedelta(hours=1))
            == 1
        )


# ----------------------------------------------------------------------------- trusted_devices


def _device(user_id: UUID, device_hash: str, clock: FakeClock, days: int = 180) -> TrustedDevice:
    now = clock.now()
    return TrustedDevice(uuid7(), user_id, device_hash, now, now + timedelta(days=days))


async def test_trusted_device_lifecycle(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    user_id = await _user(factory, fake_clock)
    async with _uow(factory, fake_clock) as uow:
        repo = uow.trusted_devices
        assert not await repo.is_trusted(user_id, "d1", fake_clock.now())
        await repo.add(_device(user_id, "d1", fake_clock, days=1))
        assert await repo.is_trusted(user_id, "d1", fake_clock.now())
        assert not await repo.is_trusted(uuid7(), "d1", fake_clock.now())
        # срок истёк
        assert not await repo.is_trusted(user_id, "d1", fake_clock.now() + timedelta(days=1))

        # повтор продлевает срок и снимает отзыв (одна строка на пару)
        assert await repo.revoke_for_user(user_id, fake_clock.now()) == 1
        assert not await repo.is_trusted(user_id, "d1", fake_clock.now())
        await repo.add(_device(user_id, "d1", fake_clock, days=180))
        assert await repo.is_trusted(user_id, "d1", fake_clock.now() + timedelta(days=100))
        await uow.commit()


async def test_revoke_for_user_except_hash(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    user_id = await _user(factory, fake_clock)
    now = fake_clock.now()
    async with _uow(factory, fake_clock) as uow:
        repo = uow.trusted_devices
        for h in ("keep", "x", "y"):
            await repo.add(_device(user_id, h, fake_clock))
        assert await repo.revoke_for_user(user_id, now, except_hash="keep") == 2
        assert await repo.is_trusted(user_id, "keep", now)
        assert not await repo.is_trusted(user_id, "x", now)
        # повторный отзыв уже отозванных ничего не меняет
        assert await repo.revoke_for_user(user_id, now, except_hash="keep") == 0
        assert await repo.revoke_for_user(user_id, now) == 1
        assert not await repo.is_trusted(user_id, "keep", now)


# ----------------------------------------------------------------------------- ревью безопасности


async def test_delete_for_subjects_removes_user_and_address_codes_only(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    user_id = await _user(factory, fake_clock)
    mine = _challenge(fake_clock, subject="user-hash", user_id=user_id)
    by_address = _challenge(
        fake_clock, subject="addr-hash", purpose=ChallengePurpose.REGISTER, user_id=None
    )
    foreign = _challenge(fake_clock, subject="other", purpose=ChallengePurpose.RESET)
    for challenge in (mine, by_address, foreign):
        await _store(factory, fake_clock, challenge)

    async with _uow(factory, fake_clock) as uow:
        deleted = await uow.email_challenges.delete_for_subjects(user_id, ["addr-hash"])
        await uow.commit()

    assert deleted == 2
    async with _uow(factory, fake_clock) as uow:
        assert await uow.email_challenges.get(mine.id) is None
        assert await uow.email_challenges.get(by_address.id) is None
        assert await uow.email_challenges.get(foreign.id) is not None


async def test_delete_for_user_removes_devices(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    user_id = await _user(factory, fake_clock)
    async with _uow(factory, fake_clock) as uow:
        await uow.trusted_devices.add(_device(user_id, "d1", fake_clock))
        await uow.trusted_devices.add(_device(user_id, "d2", fake_clock))
        assert await uow.trusted_devices.delete_for_user(user_id) == 2
        assert not await uow.trusted_devices.is_trusted(user_id, "d1", fake_clock.now())
        await uow.commit()


async def test_schema_rejects_inconsistent_codes(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    """Инварианты держит схема (миграция 0011), а не только код."""
    from dataclasses import replace

    from sqlalchemy.exc import IntegrityError

    user_id = await _user(factory, fake_clock)
    base = _challenge(fake_clock, user_id=user_id)
    bad = {
        "expires не позже created": replace(base, expires_at=base.created_at),
        "attempts больше лимита": replace(base, attempts=base.max_attempts + 1),
        "погашен и отменён": replace(
            base, consumed_at=base.created_at, cancelled_at=base.created_at
        ),
        "login без пользователя": replace(base, purpose=ChallengePurpose.LOGIN, user_id=None),
    }
    for title, challenge in bad.items():
        with pytest.raises(IntegrityError):
            async with _uow(factory, fake_clock) as uow:
                await uow.email_challenges.add(challenge)
                await uow.commit()
        assert title  # имя случая попадёт в вывод pytest при падении


async def test_purger_removes_old_codes_and_dead_devices_only(
    factory: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    from identity_service.infrastructure.db.challenge_purger import SqlChallengePurger

    user_id = await _user(factory, fake_clock)
    old = _challenge(fake_clock, subject="old", user_id=user_id)
    await _store(factory, fake_clock, old)
    async with _uow(factory, fake_clock) as uow:
        await uow.trusted_devices.add(_device(user_id, "expired", fake_clock, days=1))
        await uow.trusted_devices.add(_device(user_id, "alive", fake_clock, days=365))
        await uow.trusted_devices.add(_device(user_id, "revoked-long-ago", fake_clock, days=365))
        await uow.trusted_devices.revoke_for_user(user_id, fake_clock.now(), except_hash="alive")
        await uow.commit()
    fake_clock.advance(days=3)
    fresh = _challenge(fake_clock, subject="fresh", user_id=user_id)
    await _store(factory, fake_clock, fresh)
    now = fake_clock.now()

    purged = await SqlChallengePurger(factory).purge(
        challenges_before=now - timedelta(days=2),
        devices_revoked_before=now - timedelta(days=1),
        now=now,
        limit=100,
    )

    # старый код + истёкшее и давно отозванное устройства
    assert purged == 3
    async with _uow(factory, fake_clock) as uow:
        assert await uow.email_challenges.get(old.id) is None
        assert await uow.email_challenges.get(fresh.id) is not None
        assert await uow.trusted_devices.is_trusted(user_id, "alive", now)
