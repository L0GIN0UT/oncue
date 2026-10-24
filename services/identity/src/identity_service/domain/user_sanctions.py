"""Санкции игрока в identity: проекция события ``social.UserSanctionsChanged`` (ADR 0052).

Источник правды - social; здесь снимок для выпуска токена и бана аккаунта. Порядок событий
решает ``version``: событие с ``version`` не больше применённой отбрасывается.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from gv_core.sanctions import SanctionEntry, effective_mask, is_account_banned


@dataclass(frozen=True, slots=True)
class UserSanctions:
    """Снимок действующих санкций игрока.

    ``entries`` - записи с их сроками: маска и бан считаются на момент чтения, поэтому истёкшая
    санкция не попадает в токен, даже если событие об истечении ещё не дошло.
    ``mask``, ``account_banned``, ``next_expiry_at`` - значения самого события (для отладки и
    запросов по сроку).
    """

    user_id: UUID
    version: int
    entries: tuple[SanctionEntry, ...]
    mask: int
    account_banned: bool
    next_expiry_at: datetime | None
    updated_at: datetime

    def mask_at(self, now: datetime) -> int:
        """Маска claim ``sanctions`` без истёкших записей."""
        return effective_mask(self.entries, now)

    def banned_at(self, now: datetime) -> bool:
        """Действует ли бан аккаунта."""
        return is_account_banned(self.entries, now)
