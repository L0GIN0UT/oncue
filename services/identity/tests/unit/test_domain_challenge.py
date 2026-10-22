"""Правила кодов (домен) и HMAC-сервис кодов: примеры и property-based (Hypothesis)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from identity_service.application.code_service import HmacCodeService
from identity_service.domain import ChallengePurpose, EmailChallenge

pytestmark = pytest.mark.unit

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
KEY = b"k" * 32


def _challenge(**kw: object) -> EmailChallenge:
    base: dict[str, object] = {
        "id": uuid4(),
        "purpose": ChallengePurpose.LOGIN,
        "subject_hash": "s",
        "user_id": None,
        "code_hash": "h",
        "attempts": 0,
        "max_attempts": 5,
        "send_count": 1,
        "device_hash": None,
        "session_id": None,
        "created_at": T0,
        "expires_at": T0 + timedelta(seconds=600),
        "last_sent_at": T0,
    }
    base.update(kw)
    return EmailChallenge(**base)  # type: ignore[arg-type]  # словарь собран из полей датакласса


# ------------------------------------------------------------------------ домен


def test_active_until_expiry_consumption_or_cancel() -> None:
    ch = _challenge()
    assert ch.is_active(T0)
    assert ch.is_active(T0 + timedelta(seconds=599))
    assert not ch.is_active(T0 + timedelta(seconds=600))
    assert not _challenge(consumed_at=T0).is_active(T0)
    assert not _challenge(cancelled_at=T0).is_active(T0)


def test_expired_excludes_consumed_and_cancelled() -> None:
    late = T0 + timedelta(hours=1)
    assert _challenge().is_expired(late)
    assert not _challenge(consumed_at=T0).is_expired(late)
    assert not _challenge(cancelled_at=T0).is_expired(late)


def test_resend_interval() -> None:
    ch = _challenge()
    assert ch.resend_wait_s(T0, 60) == 60
    assert ch.resend_wait_s(T0 + timedelta(seconds=59, milliseconds=500), 60) == 1
    assert ch.resend_wait_s(T0 + timedelta(seconds=60), 60) == 0


@given(
    attempts=st.integers(0, 50),
    max_attempts=st.integers(1, 20),
    extra=st.integers(0, 5),
)
def test_attempts_left_is_monotone_and_never_negative(
    attempts: int, max_attempts: int, extra: int
) -> None:
    before = _challenge(attempts=attempts, max_attempts=max_attempts)
    after = _challenge(attempts=attempts + extra, max_attempts=max_attempts)
    assert after.attempts_left <= before.attempts_left
    assert after.attempts_left >= 0
    assert before.is_exhausted == (before.attempts_left == 0)


@given(elapsed=st.integers(0, 10_000), resend_after=st.integers(1, 600))
def test_resend_wait_never_negative_and_decreases(elapsed: int, resend_after: int) -> None:
    ch = _challenge()
    now = T0 + timedelta(seconds=elapsed)
    later = now + timedelta(seconds=1)
    assert 0 <= ch.resend_wait_s(later, resend_after) <= ch.resend_wait_s(now, resend_after)


# ------------------------------------------------------------------------ HMAC-сервис


@given(length=st.integers(1, 10))
def test_generate_has_exactly_length_digits(length: int) -> None:
    code = HmacCodeService(KEY).generate(length)
    assert len(code) == length
    assert code.isascii() and code.isdigit()


def test_generate_keeps_leading_zeros(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("identity_service.application.code_service.secrets.randbelow", lambda _: 42)
    assert HmacCodeService(KEY).generate(6) == "000042"


def test_generate_rejects_non_positive_length() -> None:
    with pytest.raises(ValueError, match="длина"):
        HmacCodeService(KEY).generate(0)


def test_short_key_rejected() -> None:
    with pytest.raises(ValueError, match="ключ"):
        HmacCodeService(b"short")


@given(code=st.text(max_size=12), other=st.text(max_size=12))
def test_digest_matches_only_same_code_and_id(code: str, other: str) -> None:
    svc = HmacCodeService(KEY)
    cid: UUID = uuid4()
    stored = svc.digest(challenge_id=cid, code=code)
    assert svc.matches(stored, challenge_id=cid, code=code)
    assert svc.matches(stored, challenge_id=cid, code=other) == (code == other)
    assert not svc.matches(stored, challenge_id=uuid4(), code=code)


def test_digest_depends_on_key_and_hides_code() -> None:
    cid = uuid4()
    a = HmacCodeService(KEY).digest(challenge_id=cid, code="123456")
    b = HmacCodeService(b"z" * 32).digest(challenge_id=cid, code="123456")
    assert a != b
    assert "123456" not in a


def test_non_ascii_input_does_not_crash() -> None:
    svc = HmacCodeService(KEY)
    cid = uuid4()
    stored = svc.digest(challenge_id=cid, code="123456")
    assert not svc.matches(stored, challenge_id=cid, code="１２３４５６")  # полноширинные цифры
