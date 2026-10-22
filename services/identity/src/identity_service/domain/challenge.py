"""Одноразовые коды из писем и доверенные устройства (ADR 0041): правила без IO.

Время приходит параметром (``now``) — домен не обращается к часам. Хранение кода (HMAC) и
случайность — в application/infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from math import ceil
from uuid import UUID


class ChallengePurpose(StrEnum):
    """Назначение одноразового кода из письма."""

    REGISTER = "register"
    LOGIN = "login"
    RESET = "reset"
    PASSWORD_CHANGE = "password_change"  # noqa: S105 — имя назначения, не секрет


@dataclass(frozen=True, slots=True)
class EmailChallenge:
    """Выданный код: в хранилище только HMAC, сам код знает лишь получатель письма.

    Attributes:
        subject_hash: Хэш субъекта — адрес для register/reset, ``user_id`` для login и
            password_change; вместе с ``purpose`` задаёт единственный активный код.
        user_id: Владелец; ``None`` у «пустышки» (адрес занят/не существует).
        attempts / max_attempts: Списанные и допустимые вводы.
        send_count: Сколько раз код отправлялся (первая отправка + resend).
        device_hash: sha256 одноразового cookie ``gv_bind`` браузера, запросившего код
            (register, login): подтвердить код можно только из него; ``None`` — без привязки.
        mail_key: Ключ лимита писем на адрес (хэш адреса) — одинаков у настоящего кода и
            пустышки, поэтому resend расходует один и тот же лимит.
        session_id: Сессия, начавшая смену пароля (её не отзывают).
    """

    id: UUID
    purpose: ChallengePurpose
    subject_hash: str
    user_id: UUID | None
    code_hash: str
    attempts: int
    max_attempts: int
    send_count: int
    device_hash: str | None
    session_id: UUID | None
    created_at: datetime
    expires_at: datetime
    last_sent_at: datetime
    consumed_at: datetime | None = None
    cancelled_at: datetime | None = None
    mail_key: str | None = None

    def is_active(self, now: datetime) -> bool:
        """Не погашен, не отменён и не истёк."""
        return self.consumed_at is None and self.cancelled_at is None and now < self.expires_at

    def is_expired(self, now: datetime) -> bool:
        """Срок вышел (погашенный и отменённый код истёкшим не считается)."""
        return self.consumed_at is None and self.cancelled_at is None and now >= self.expires_at

    @property
    def attempts_left(self) -> int:
        """Сколько вводов осталось (не меньше нуля)."""
        return max(0, self.max_attempts - self.attempts)

    @property
    def is_exhausted(self) -> bool:
        """Попытки исчерпаны."""
        return self.attempts >= self.max_attempts

    def resend_wait_s(self, now: datetime, resend_after_s: int) -> int:
        """Сколько секунд ждать до повторной отправки (0 — можно сейчас)."""
        remaining = (self.last_sent_at + timedelta(seconds=resend_after_s) - now).total_seconds()
        return max(0, ceil(remaining))

    def expires_in_s(self, now: datetime) -> int:
        """Сколько секунд код ещё действует (не меньше нуля)."""
        return max(0, ceil((self.expires_at - now).total_seconds()))


@dataclass(frozen=True, slots=True)
class TrustedDevice:
    """Доверенное устройство пользователя (в хранилище только sha256 cookie)."""

    id: UUID
    user_id: UUID
    device_hash: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
