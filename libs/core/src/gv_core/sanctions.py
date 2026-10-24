"""Маска санкций: виды -> биты claim, действующие записи, объединение (ADR 0052).

Чистые функции без IO и зависимостей - доступны доменам сервисов (identity, match, social).
Виды санкций - строки события ``social.UserSanctionsChanged`` (``ranked|chat|online|account``);
``account`` в маску не входит (флаг бана аккаунта). Биты совпадают с ``gv_auth.Sanction``
(равенство проверяет тест ``libs/auth``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

ACCOUNT_KIND: Final = "account"

RANKED_BIT: Final = 1
CHAT_MUTE_BIT: Final = 8
COMMENTS_BIT: Final = 16
ONLINE_BIT: Final = 32

SANCTION_KIND_MASK: Final[Mapping[str, int]] = {
    "ranked": RANKED_BIT,
    "chat": CHAT_MUTE_BIT | COMMENTS_BIT,
    "online": ONLINE_BIT,
    ACCOUNT_KIND: 0,
}
"""Биты claim ``sanctions`` по виду санкции."""


@dataclass(frozen=True, slots=True)
class SanctionEntry:
    """Действующая (на момент события) санкция: вид и срок (``None`` - бессрочно)."""

    kind: str
    expires_at: datetime | None

    def is_live(self, now: datetime) -> bool:
        """Срок не вышел (граница - уже не действует)."""
        return self.expires_at is None or self.expires_at > now


def mask_of_kinds(kinds: Iterable[str]) -> int:
    """Маска claim по видам; неизвестные виды (будущие) игнорируются."""
    mask = 0
    for kind in kinds:
        mask |= SANCTION_KIND_MASK.get(kind, 0)
    return mask


def effective_mask(entries: Iterable[SanctionEntry], now: datetime) -> int:
    """Маска только по ещё не истёкшим записям."""
    return mask_of_kinds(e.kind for e in entries if e.is_live(now))


def is_account_banned(entries: Iterable[SanctionEntry], now: datetime) -> bool:
    """Есть ли действующий бан аккаунта."""
    return any(e.kind == ACCOUNT_KIND and e.is_live(now) for e in entries)


def merge_masks(*masks: int) -> int:
    """Объединить маски (claim | сторонний источник): запрет любого источника действует."""
    merged = 0
    for mask in masks:
        merged |= mask
    return merged


def parse_entries(active: Iterable[Mapping[str, Any]]) -> tuple[SanctionEntry, ...]:
    """Записи из поля ``active`` события ``UserSanctionsChanged``.

    Raises:
        KeyError: нет ``kind`` или ``expires_at``.
        ValueError: ``expires_at`` не ISO-время или без часового пояса.
        TypeError: ``kind`` не строка.
    """
    parsed: list[SanctionEntry] = []
    for item in active:
        raw = item["expires_at"]
        expires: datetime | None = None
        if raw is not None:
            expires = datetime.fromisoformat(raw)
            if expires.tzinfo is None:
                raise ValueError("expires_at без часового пояса")
        kind = item["kind"]
        if not isinstance(kind, str):
            raise TypeError("kind должен быть строкой")
        parsed.append(SanctionEntry(kind=kind, expires_at=expires))
    return tuple(parsed)
