"""Динамические профили ассетов (ADR 0025): заведены по ``game.ClueAssetsRequested``.

::

    (запрос) ──▶ backfilling ──досоздание наборов порциями──▶ active
        │
        └──(параметры неверны, хэш не сходится, лимит)──▶ rejected ──(повтор при свободном
                                                                       лимите)──▶ backfilling

Профиль уникален по ``(clue_type, params_hash, domain)``: повторный запрос тех же параметров
ничего не создаёт. Отклонённый из-за лимита профиль оживает повторным запросом, когда лимит
освободился; отклонённый из-за параметров — нет (те же параметры снова будут неверны).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from gv_core.domain import Entity
from gv_core.errors import DomainError

DYNAMIC_PROFILE_PREFIX = "dyn_"
_KEY_HASH_CHARS = 16
LIMIT_REACHED = "profile.limit_reached"


class DynamicProfileStatus(StrEnum):
    """Состояние динамического профиля."""

    BACKFILLING = "backfilling"
    ACTIVE = "active"
    REJECTED = "rejected"


def dynamic_profile_key(params_hash: str) -> str:
    """Ключ профиля в наборах и ``AssetsReady``: ``dyn_<первые 16 hex params_hash>``.

    ``AssetsReady.profile_key`` обязателен по контракту; game ищет ассеты по
    ``params_hash``, ключ нужен только для уникальности набора и для людей.
    """
    return f"{DYNAMIC_PROFILE_PREFIX}{params_hash[:_KEY_HASH_CHARS]}"


class DynamicProfile(Entity[UUID]):
    """Динамический профиль одного типа загадки для одного домена.

    Attributes:
        clue_type: Тип загадки.
        domain: Домен каталога (``music`` | ``film``).
        params: Параметры уровней в присланном виде (по ним считан ``params_hash``).
        params_hash: ``params_hash(params)``.
        status: Состояние.
        error_code: Почему отклонён (``rejected``).
        request_id: Последний запрос, изменивший профиль.
        mode_key: Режим, из черновика которого пришёл запрос.
        actor_id: Кто запросил.
        backfill_cursor: Последняя обработанная единица досоздания (по ``media_item_id``).
        created_at: Когда заведён.
        updated_at: Когда менялся.
    """

    def __init__(
        self,
        id: UUID,  # noqa: A002
        *,
        clue_type: str,
        domain: str,
        params: dict[str, Any],
        params_hash: str,
        status: DynamicProfileStatus,
        error_code: str | None,
        request_id: UUID,
        mode_key: str,
        actor_id: UUID,
        backfill_cursor: UUID | None,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        super().__init__(id)
        if (status is DynamicProfileStatus.REJECTED) != (error_code is not None):
            raise DomainError(
                "код ошибки есть только у отклонённого профиля", code="profile.invalid_state"
            )
        self.clue_type = clue_type
        self.domain = domain
        self.params = params
        self.params_hash = params_hash
        self.status = status
        self.error_code = error_code
        self.request_id = request_id
        self.mode_key = mode_key
        self.actor_id = actor_id
        self.backfill_cursor = backfill_cursor
        self.created_at = created_at
        self.updated_at = updated_at

    @property
    def profile_key(self) -> str:
        """Ключ профиля в наборах ассетов."""
        return dynamic_profile_key(self.params_hash)

    @property
    def is_usable(self) -> bool:
        """По профилю строятся наборы (досоздание идёт или закончено)."""
        return self.status is not DynamicProfileStatus.REJECTED

    def reject(self, error_code: str, *, request_id: UUID, now: datetime) -> None:
        """Отклонить (при повторе — с кодом последней причины)."""
        self.status = DynamicProfileStatus.REJECTED
        self.error_code = error_code[:64]
        self.request_id = request_id
        self.updated_at = now

    def revive(self, *, request_id: UUID, actor_id: UUID, mode_key: str, now: datetime) -> None:
        """Повторный запрос отклонённого по лимиту профиля: досоздание с начала."""
        if self.status is not DynamicProfileStatus.REJECTED:
            return
        self.status = DynamicProfileStatus.BACKFILLING
        self.error_code = None
        self.request_id, self.actor_id, self.mode_key = request_id, actor_id, mode_key
        self.backfill_cursor = None
        self.updated_at = now

    def advance(self, cursor: UUID | None, *, done: bool, now: datetime) -> None:
        """Порция досоздания обработана; ``done`` — единиц домена больше нет."""
        if self.status is not DynamicProfileStatus.BACKFILLING:
            return
        if cursor is not None:
            self.backfill_cursor = cursor
        if done:
            self.status = DynamicProfileStatus.ACTIVE
        self.updated_at = now
