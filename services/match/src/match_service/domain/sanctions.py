"""Санкции игрока в match: проекция ``social.UserSanctionsChanged`` и эффективная маска (ADR 0052).

Правило эффективной маски: **проекция авторитетна, если у игрока есть её запись; иначе claim**.
Claim в токене живёт до access TTL и устаревает: снятую санкцию он продолжал бы «держать», а
выданную позже - не знал бы. Проекция обновляется событием за секунды и считается на момент
запроса (истёкшие записи не входят).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from gv_core.sanctions import (
    CHAT_MUTE_BIT,
    COMMENTS_BIT,
    ONLINE_BIT,
    RANKED_BIT,
    SanctionEntry,
    effective_mask,
)

ONLINE_BAN = ONLINE_BIT
RANKED_BAN = RANKED_BIT
CHAT_MUTE_BAN = CHAT_MUTE_BIT | COMMENTS_BIT


@dataclass(frozen=True, slots=True)
class SanctionsSnapshot:
    """Снимок санкций игрока из события (``version`` монотонна)."""

    user_id: UUID
    version: int
    entries: tuple[SanctionEntry, ...]
    mask: int
    next_expiry_at: datetime | None

    def mask_at(self, now: datetime) -> int:
        """Маска по ещё не истёкшим записям."""
        return effective_mask(self.entries, now)


def effective_sanctions(snapshot: SanctionsSnapshot | None, claim: int, now: datetime) -> int:
    """Маска санкций для решения: проекция, если запись есть, иначе claim токена."""
    if snapshot is None:
        return claim
    return snapshot.mask_at(now)
