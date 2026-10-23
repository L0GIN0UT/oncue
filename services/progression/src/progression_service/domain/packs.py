"""Паки косметики: модель и проверки CRUD (ADR 0045; продажа - этап 6, billing выключен)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from gv_core.errors import ConflictError, InvalidInputError, NotFoundError


class PackNotFoundError(NotFoundError):
    """Пака нет (404)."""

    default_code = "progression.pack_not_found"

    def __init__(self) -> None:
        super().__init__("пак не найден")


class PackKeyTakenError(ConflictError):
    """Ключ пака занят (409)."""

    default_code = "progression.pack_key_taken"

    def __init__(self) -> None:
        super().__init__("пак с таким ключом уже есть")


class PackInvalidError(InvalidInputError):
    """Состав пака неверен: пустой или с повторами (422)."""

    default_code = "progression.pack_invalid"

    def __init__(self, reason: str) -> None:
        super().__init__(f"состав пака неверен: {reason}")


class BillingDisabledError(NotFoundError):
    """Витрина паков скрыта, пока ``billing.enabled=false`` (404)."""

    default_code = "progression.billing_disabled"

    def __init__(self) -> None:
        super().__init__("продажа паков недоступна")


@dataclass(frozen=True, slots=True)
class Pack:
    """Пак: набор предметов и Версы за цену в копейках."""

    id: UUID
    key: str
    title: str
    style: str
    item_ids: tuple[UUID, ...]
    verses_amount: int
    price_kopecks: int
    active: bool
    created_at: datetime
    updated_at: datetime


def check_pack_items(item_ids: Sequence[UUID]) -> None:
    """Состав непуст и без повторов.

    Raises:
        PackInvalidError: пустой состав или повторяющийся предмет.
    """
    if not item_ids:
        raise PackInvalidError("нужен хотя бы один предмет")
    if len(set(item_ids)) != len(item_ids):
        raise PackInvalidError("предметы не должны повторяться")
