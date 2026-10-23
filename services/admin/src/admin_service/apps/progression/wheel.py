"""Предпросмотр вероятностей колеса: веса секторов -> шансы (ADR 0044).

Считается здесь, из тела конфига, без вызова API: админ видит, как изменится колесо, до
публикации. Правила те же, что у игрового колеса: шанс сектора = ``weight / сумма весов``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SectorChance:
    """Сектор колеса и его шанс."""

    key: str
    title: str
    weight: int
    chance: float
    prize: str


def _prize(raw: Any) -> str:
    if not isinstance(raw, dict):
        return "-"
    kind = raw.get("kind")
    if kind in ("xp", "verses"):
        return f"{raw.get('amount', 0)} {'опыта' if kind == 'xp' else 'Версов'}"
    if kind == "boost":
        return f"буст x{raw.get('multiplier')} на {raw.get('matches')} матчей"
    if kind == "item":
        return f"предмет {raw.get('item_key')}"
    return str(kind or "-")


def sector_chances(config: Any) -> list[SectorChance]:
    """Шансы секторов колеса из тела конфига.

    Секторы с нечисловым или неположительным весом пропускаются (сервис такой конфиг не
    сохранит; страница не должна падать на старом теле).
    """
    wheel = config.get("wheel") if isinstance(config, dict) else None
    raw = wheel.get("sectors") if isinstance(wheel, dict) else None
    sectors = [
        s
        for s in (raw if isinstance(raw, list) else [])
        if isinstance(s, dict)
        and isinstance(s.get("weight"), int)
        and not isinstance(s.get("weight"), bool)
        and s["weight"] > 0
    ]
    total = sum(int(s["weight"]) for s in sectors)
    return [
        SectorChance(
            key=str(s.get("key", "")),
            title=str(s.get("title", "")),
            weight=int(s["weight"]),
            chance=int(s["weight"]) / total,
            prize=_prize(s.get("prize")),
        )
        for s in sectors
    ]
