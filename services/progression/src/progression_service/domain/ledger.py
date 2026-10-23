"""Проводки журналов опыта и Версов (ADR 0042, 0043): зона повышенной безопасности.

Журнал append-only; проводка — неизменяемая запись с ``op_id`` из предмета операции. Баланс —
сумма проводок бакета и не бывает ниже нуля. Сторно — встречная проводка с ``reversal_of``;
у проводки не больше одного сторно, сторно сторно не делают.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from gv_core.errors import ConflictError, DomainError


class InsufficientBalanceError(ConflictError):
    """Баланс ушёл бы ниже нуля (409)."""

    default_code = "progression.insufficient_balance"


class AlreadyReversedError(ConflictError):
    """У проводки уже есть сторно (409)."""

    default_code = "progression.already_reversed"


class NotReversibleError(DomainError):
    """Проводку нельзя сторнировать (само сторно)."""

    default_code = "progression.not_reversible"


class Direction(StrEnum):
    """Знак проводки."""

    CREDIT = "credit"
    DEBIT = "debit"

    @property
    def opposite(self) -> Direction:
        """Встречное направление (для сторно)."""
        return Direction.DEBIT if self is Direction.CREDIT else Direction.CREDIT


class Bucket(StrEnum):
    """Бакет Версов: заработаны игрой или куплены (биллинг выключен, ADR 0043)."""

    EARNED = "earned"
    PAID = "paid"


@dataclass(frozen=True, slots=True)
class Entry:
    """Проводка журнала Версов.

    Attributes:
        op_id: Идентификатор операции из предмета (идемпотентность, ``UNIQUE``).
        user_id: Владелец.
        direction: Кредит или дебет.
        amount: Целая положительная сумма.
        bucket: Бакет баланса.
        reason: Ключ причины из конфига.
        source_ref: Предмет операции (id сессии, матча, недели).
        reversal_of: ``op_id`` исходной проводки, если это сторно.
        actor_id: Админ (ручные операции); у автоматических ``None``.
        config_version: Версия конфига, по которой посчитано.
        at: Момент проводки.
    """

    op_id: str
    user_id: UUID
    direction: Direction
    amount: int
    bucket: Bucket
    reason: str
    source_ref: str
    config_version: int
    at: datetime
    reversal_of: str | None = None
    actor_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise DomainError("сумма проводки должна быть положительной", code="progression.amount")
        if not self.op_id:
            raise DomainError("у проводки нужен op_id", code="progression.op_id")

    @property
    def signed(self) -> int:
        """Влияние на баланс: плюс у кредита, минус у дебета."""
        return self.amount if self.direction is Direction.CREDIT else -self.amount


def credit(
    *,
    op_id: str,
    user_id: UUID,
    amount: int,
    reason: str,
    source_ref: str,
    config_version: int,
    at: datetime,
    bucket: Bucket = Bucket.EARNED,
    actor_id: UUID | None = None,
) -> Entry:
    """Начисление."""
    return Entry(
        op_id=op_id,
        user_id=user_id,
        direction=Direction.CREDIT,
        amount=amount,
        bucket=bucket,
        reason=reason,
        source_ref=source_ref,
        config_version=config_version,
        at=at,
        actor_id=actor_id,
    )


def debit(
    *,
    op_id: str,
    user_id: UUID,
    amount: int,
    reason: str,
    source_ref: str,
    config_version: int,
    at: datetime,
    bucket: Bucket = Bucket.EARNED,
    actor_id: UUID | None = None,
) -> Entry:
    """Списание."""
    return Entry(
        op_id=op_id,
        user_id=user_id,
        direction=Direction.DEBIT,
        amount=amount,
        bucket=bucket,
        reason=reason,
        source_ref=source_ref,
        config_version=config_version,
        at=at,
        actor_id=actor_id,
    )


def reversal(
    original: Entry,
    *,
    op_id: str,
    actor_id: UUID,
    reason: str,
    at: datetime,
    config_version: int,
    already_reversed: bool,
) -> Entry:
    """Сторно: встречная проводка той же суммы и бакета.

    Raises:
        NotReversibleError: исходная проводка сама сторно.
        AlreadyReversedError: сторно уже есть.
    """
    if original.reversal_of is not None:
        raise NotReversibleError("сторно нельзя сторнировать")
    if already_reversed:
        raise AlreadyReversedError("проводка уже сторнирована")
    return Entry(
        op_id=op_id,
        user_id=original.user_id,
        direction=original.direction.opposite,
        amount=original.amount,
        bucket=original.bucket,
        reason=reason,
        source_ref=original.op_id,
        config_version=config_version,
        at=at,
        reversal_of=original.op_id,
        actor_id=actor_id,
    )


def apply_to_balance(balance: int, entry: Entry) -> int:
    """Баланс бакета после проводки.

    Raises:
        InsufficientBalanceError: результат был бы ниже нуля.
    """
    result = balance + entry.signed
    if result < 0:
        raise InsufficientBalanceError("недостаточно средств на балансе")
    return result
