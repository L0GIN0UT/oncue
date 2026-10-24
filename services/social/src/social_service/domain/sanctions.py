"""Санкции: маска claim, пределы срока, активность, событие ``UserSanctionsChanged`` (ADR 0052).

Чистые функции без IO. Правила:

* ``ranked`` -> ``RANKED``; ``chat`` -> ``CHAT_MUTE | COMMENTS``; ``online`` -> ``ONLINE``;
  ``account`` в маску не входит (флаг ``account_banned``);
* одна действующая запись на ``(игрок, вид)``: новая выдача заменяет прежнюю (та закрывается
  с причиной ``replaced``), поэтому дублей и «суммирования» сроков нет;
* срок - ``None`` (бессрочно) или в пределах ``config.sanctions``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Final, Literal
from uuid import UUID

from gv_core.domain import DomainEvent
from gv_core.sanctions import SANCTION_KIND_MASK, mask_of_kinds

Kind = Literal["ranked", "chat", "online", "account"]

KIND_MASK: Final = SANCTION_KIND_MASK
"""Биты claim по виду санкции (``account`` - флаг, не бит); общая таблица ``gv_auth``."""

SELF_SANCTION_FORBIDDEN = "social.self_sanction_forbidden"
ACCOUNT_SANCTION_FORBIDDEN = "social.account_sanction_forbidden"
DURATION_INVALID = "social.sanction_duration_invalid"
SANCTION_NOT_FOUND = "social.sanction_not_found"
PLAYER_NOT_FOUND = "social.player_not_found"
ACCOUNT_PERM = "social.sanctions.account"

REVOKE_REPLACED = "replaced"
REVOKE_EXPIRED = "expired"
SOCIAL_AGGREGATE = "social"


def mask_of(kinds: Iterable[str]) -> int:
    """Маска claim ``sanctions`` по видам действующих санкций."""
    return mask_of_kinds(kinds)


def is_active(expires_at: datetime | None, revoked_at: datetime | None, now: datetime) -> bool:
    """Действует ли запись: не снята и срок не вышел (граница срока - уже не действует)."""
    return revoked_at is None and (expires_at is None or expires_at > now)


def duration_in_bounds(duration_s: int | None, *, min_s: int, max_s: int) -> bool:
    """Срок в пределах конфига; ``None`` (бессрочно) допустим всегда."""
    return duration_s is None or min_s <= duration_s <= max_s


def next_expiry(expiries: Iterable[datetime | None]) -> datetime | None:
    """Ближайшее истечение среди срочных; ``None`` - срочных нет."""
    dated = [e for e in expiries if e is not None]
    return min(dated) if dated else None


@dataclass(frozen=True, slots=True)
class ActiveSanction:
    """Действующая санкция в событии (без причины)."""

    expires_at: datetime | None
    kind: str
    sanction_id: UUID


@dataclass(frozen=True, kw_only=True)
class UserSanctionsChanged(DomainEvent):
    """Изменился набор действующих санкций игрока (``contracts/events/social``)."""

    aggregate_type: ClassVar[str] = SOCIAL_AGGREGATE

    account_banned: bool
    active: list[ActiveSanction]
    at: datetime
    changed_by: UUID | None
    mask: int
    next_expiry_at: datetime | None
    user_id: UUID
    version: int
