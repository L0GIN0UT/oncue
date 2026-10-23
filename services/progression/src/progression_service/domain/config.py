"""Конфиг progression на одну версию и его семантическая проверка (ADR 0042–0044).

Схема — ``contracts/progression/progression_config.v1.json``. Здесь разбирается то, что нужно
начислению (P1): уровни, опыт, Версы, сетки мест, часовой пояс. Остальные блоки (задания,
бейджи, колесо, лимиты выдачи) хранятся как есть в ``body`` и разбираются своими потоками;
общий валидатор проверяет лишь то, что опасно для всех (веса колеса > 0).

В коде значений нет: любая сумма, порог и граница приходят из версии конфига.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gv_core.errors import InvalidInputError

CONFIG_INVALID = "progression.config_invalid"

_AMOUNT_MAX = 100_000_000
BOUNDS: Mapping[str, int | float] = {
    "min_rounds_ratio": 1,
    "levels.base": _AMOUNT_MAX,
    "levels.step": _AMOUNT_MAX,
    "levels.max_level": 1000,
    "xp.solo_round.base": _AMOUNT_MAX,
    "xp.solo_round.max": _AMOUNT_MAX,
    "xp.solo_round.score_divisor": _AMOUNT_MAX,
    "xp.daily.base": _AMOUNT_MAX,
    "xp.daily.per_streak": _AMOUNT_MAX,
    "xp.daily.max": _AMOUNT_MAX,
    "xp.duel.participation": _AMOUNT_MAX,
    "xp.duel.per_round_won": _AMOUNT_MAX,
    "xp.duel.win_bonus": _AMOUNT_MAX,
    "xp.party.participation": _AMOUNT_MAX,
    "xp.party.place_bonus[]": _AMOUNT_MAX,
    "xp.party.other_place_bonus": _AMOUNT_MAX,
    "xp.first_win_of_day.bonus": _AMOUNT_MAX,
    "xp.solo_daily_cap": _AMOUNT_MAX,
    "verses.solo_round": _AMOUNT_MAX,
    "verses.daily": _AMOUNT_MAX,
    "verses.duel.win": _AMOUNT_MAX,
    "verses.duel.loss": _AMOUNT_MAX,
    "verses.party_by_place[]": _AMOUNT_MAX,
    "verses.solo_daily_cap": _AMOUNT_MAX,
    "verses.weekly_top[].from": _AMOUNT_MAX,
    "verses.weekly_top[].to": _AMOUNT_MAX,
    "verses.weekly_top[].amount": _AMOUNT_MAX,
    "verses.season[].from": _AMOUNT_MAX,
    "verses.season[].to": _AMOUNT_MAX,
    "verses.season[].amount": _AMOUNT_MAX,
    "wheel.sectors[].weight": 1_000_000,
    "wheel.sectors[].prize.multiplier": 10,
}
"""Верхние границы значений: те же ``maximum``, что в схеме конфига (ключ — путь поля, ``[]`` —
элемент массива). Сверка со схемой — тест ``test_config_bounds``; схема и домен держат границу
вместе, чтобы опубликованная версия не обошла проверку (как ``ladder`` в rating)."""

ITEM_LIMITS: Mapping[str, int] = {
    "xp.party.place_bonus": 6,
    "verses.party_by_place": 6,
    "verses.weekly_top": 20,
    "verses.season": 20,
    "wheel.sectors": 64,
}
"""Верхние границы длины массивов (``maxItems`` схемы)."""

MAX_BOOST_MULTIPLIER = Decimal(str(BOUNDS["wheel.sectors[].prize.multiplier"]))
"""Наибольший множитель буста опыта, который вообще допускает конфиг."""


class ConfigInvalidError(InvalidInputError):
    """Конфиг progression нарушает схему или смысловые правила."""

    default_code = CONFIG_INVALID

    def __init__(self, message: str) -> None:
        super().__init__(message, code=CONFIG_INVALID)


@dataclass(frozen=True, slots=True)
class LevelsConfig:
    """Кривая уровней: опыт на переход к уровню ``n`` = ``base + step·n``."""

    base: int
    step: int
    max_level: int


@dataclass(frozen=True, slots=True)
class SoloRoundXp:
    """Опыт за соло-раунд: ``base + очки // score_divisor``, не больше ``max``."""

    base: int
    score_divisor: int
    max: int


@dataclass(frozen=True, slots=True)
class DailyXp:
    """Опыт за дейлик: ``base + per_streak · серия``, не больше ``max``."""

    base: int
    per_streak: int
    max: int


@dataclass(frozen=True, slots=True)
class DuelXp:
    """Опыт за дуэль."""

    participation: int
    per_round_won: int
    win_bonus: int


@dataclass(frozen=True, slots=True)
class PartyXp:
    """Опыт за party/lobby: участие и бонус по месту (подряд с первого)."""

    participation: int
    place_bonus: tuple[int, ...]
    other_place_bonus: int


@dataclass(frozen=True, slots=True)
class XpConfig:
    """Источники опыта и потолок соло."""

    solo_round: SoloRoundXp
    daily: DailyXp
    duel: DuelXp
    party: PartyXp
    first_win_bonus: int
    solo_daily_cap: int


@dataclass(frozen=True, slots=True)
class DuelVerses:
    """Версы за дуэль."""

    win: int
    loss: int


@dataclass(frozen=True, slots=True)
class Band:
    """Полоса мест сетки наград: ``first``…``last`` включительно."""

    first: int
    last: int
    amount: int


@dataclass(frozen=True, slots=True)
class VersesConfig:
    """Источники Версов, потолок соло и сетки наград рейтинга."""

    solo_round: int
    daily: int
    duel: DuelVerses
    party_by_place: tuple[int, ...]
    solo_daily_cap: int
    weekly_top: tuple[Band, ...]
    season: tuple[Band, ...]


@dataclass(frozen=True, slots=True)
class ProgressionConfig:
    """Разобранная и проверенная версия конфига.

    Attributes:
        timezone: IANA-зона суток (день события — по ``finished_at`` в этой зоне).
        levels: Кривая уровней.
        min_rounds_ratio: Доля сыгранных раундов для бонусов за результат (антиабьюз).
        xp: Опыт.
        verses: Версы.
        body: Исходное тело версии (для блоков, которые разбирают другие потоки).
    """

    timezone: str
    levels: LevelsConfig
    min_rounds_ratio: float
    xp: XpConfig
    verses: VersesConfig
    body: Mapping[str, Any]

    @property
    def tz(self) -> ZoneInfo:
        """Часовой пояс суток."""
        return ZoneInfo(self.timezone)

    @classmethod
    def parse(cls, body: Mapping[str, Any]) -> ProgressionConfig:
        """Разобрать и проверить тело версии конфига.

        Raises:
            ConfigInvalidError: ``progression.config_invalid`` — нарушена схема или смысл.
        """
        timezone = _timezone(body.get("timezone"))
        levels = _levels(_obj(body, "levels"))
        ratio = body.get("min_rounds_ratio")
        ratio_max = BOUNDS["min_rounds_ratio"]
        if (
            isinstance(ratio, bool)
            or not isinstance(ratio, int | float)
            or not 0 <= ratio <= ratio_max
        ):
            raise ConfigInvalidError("min_rounds_ratio должен быть числом от 0 до 1")
        xp = _xp(_obj(body, "xp"))
        verses = _verses(_obj(body, "verses"))
        _check_wheel(body.get("wheel"))
        return cls(
            timezone=timezone,
            levels=levels,
            min_rounds_ratio=float(ratio),
            xp=xp,
            verses=verses,
            body=dict(body),
        )


# ---------------------------------------------------------------- разбор
def _obj(parent: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = parent.get(key)
    if not isinstance(value, Mapping):
        raise ConfigInvalidError(f"{key}: ожидается объект")
    return value


def _int(parent: Mapping[str, Any], key: str, path: str, *, minimum: int = 0) -> int:
    """Целое от ``minimum`` до ``BOUNDS[path]`` (``bool`` и дроби отвергаются)."""
    value = parent.get(key)
    maximum = int(BOUNDS[path])
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ConfigInvalidError(f"{path}: ожидается целое от {minimum} до {maximum}")
    return value


def _timezone(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigInvalidError("timezone: ожидается IANA-зона")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ConfigInvalidError(f"timezone: неизвестная зона {value!r}") from exc
    return value


def _levels(raw: Mapping[str, Any]) -> LevelsConfig:
    # Порог уровня n = base + step·n при step ≥ 0 не убывает: монотонность — в неотрицательности.
    return LevelsConfig(
        base=_int(raw, "base", "levels.base"),
        step=_int(raw, "step", "levels.step"),
        max_level=_int(raw, "max_level", "levels.max_level", minimum=1),
    )


def _int_list(
    parent: Mapping[str, Any], key: str, path: str, *, min_items: int = 0
) -> tuple[int, ...]:
    """Массив сумм: длина до ``ITEM_LIMITS[path]``, элементы — ``BOUNDS[path[]]``."""
    value = parent.get(key)
    max_items = ITEM_LIMITS[path]
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ConfigInvalidError(f"{path}: ожидается массив")
    if not min_items <= len(value) <= max_items:
        raise ConfigInvalidError(f"{path}: от {min_items} до {max_items} элементов")
    maximum = int(BOUNDS[f"{path}[]"])
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= maximum:
            raise ConfigInvalidError(f"{path}: суммы — целые от 0 до {maximum}")
    return tuple(value)


def _xp(raw: Mapping[str, Any]) -> XpConfig:
    solo = _obj(raw, "solo_round")
    daily = _obj(raw, "daily")
    duel = _obj(raw, "duel")
    party = _obj(raw, "party")
    first_win = _obj(raw, "first_win_of_day")
    return XpConfig(
        solo_round=SoloRoundXp(
            base=_int(solo, "base", "xp.solo_round.base"),
            score_divisor=_int(solo, "score_divisor", "xp.solo_round.score_divisor", minimum=1),
            max=_int(solo, "max", "xp.solo_round.max"),
        ),
        daily=DailyXp(
            base=_int(daily, "base", "xp.daily.base"),
            per_streak=_int(daily, "per_streak", "xp.daily.per_streak"),
            max=_int(daily, "max", "xp.daily.max"),
        ),
        duel=DuelXp(
            participation=_int(duel, "participation", "xp.duel.participation"),
            per_round_won=_int(duel, "per_round_won", "xp.duel.per_round_won"),
            win_bonus=_int(duel, "win_bonus", "xp.duel.win_bonus"),
        ),
        party=PartyXp(
            participation=_int(party, "participation", "xp.party.participation"),
            place_bonus=_int_list(party, "place_bonus", "xp.party.place_bonus"),
            other_place_bonus=_int(party, "other_place_bonus", "xp.party.other_place_bonus"),
        ),
        first_win_bonus=_int(first_win, "bonus", "xp.first_win_of_day.bonus"),
        solo_daily_cap=_int(raw, "solo_daily_cap", "xp.solo_daily_cap"),
    )


def _bands(raw: Mapping[str, Any], key: str) -> tuple[Band, ...]:
    """Сетка мест: полосы подряд с 1, без пропусков и пересечений, ``from ≤ to``."""
    path = f"verses.{key}"
    value = raw.get(key)
    if not isinstance(value, Sequence) or isinstance(value, str | bytes) or not value:
        raise ConfigInvalidError(f"{key}: ожидается непустой массив полос")
    if len(value) > ITEM_LIMITS[path]:
        raise ConfigInvalidError(f"{key}: не больше {ITEM_LIMITS[path]} полос")
    bands: list[Band] = []
    expected = 1
    for item in value:
        if not isinstance(item, Mapping):
            raise ConfigInvalidError(f"{key}: полоса должна быть объектом")
        first = _int(item, "from", f"{path}[].from", minimum=1)
        last = _int(item, "to", f"{path}[].to", minimum=1)
        amount = _int(item, "amount", f"{path}[].amount")
        if first > last:
            raise ConfigInvalidError(f"{key}: полоса {first}–{last}: from больше to")
        if first != expected:
            raise ConfigInvalidError(
                f"{key}: полосы должны идти подряд с 1 (ожидалось место {expected}, "
                f"получено {first})"
            )
        bands.append(Band(first, last, amount))
        expected = last + 1
    return tuple(bands)


def _verses(raw: Mapping[str, Any]) -> VersesConfig:
    duel = _obj(raw, "duel")
    return VersesConfig(
        solo_round=_int(raw, "solo_round", "verses.solo_round"),
        daily=_int(raw, "daily", "verses.daily"),
        duel=DuelVerses(
            win=_int(duel, "win", "verses.duel.win"), loss=_int(duel, "loss", "verses.duel.loss")
        ),
        party_by_place=_int_list(raw, "party_by_place", "verses.party_by_place", min_items=1),
        solo_daily_cap=_int(raw, "solo_daily_cap", "verses.solo_daily_cap"),
        weekly_top=_bands(raw, "weekly_top"),
        season=_bands(raw, "season"),
    )


def _check_wheel(raw: object) -> None:
    """Колесо: число секторов, веса, множитель буста (остальное колеса проверяет поток P2)."""
    if raw is None:
        return
    if not isinstance(raw, Mapping):
        raise ConfigInvalidError("wheel: ожидается объект")
    sectors = raw.get("sectors", [])
    if not isinstance(sectors, Sequence) or isinstance(sectors, str | bytes):
        raise ConfigInvalidError("wheel.sectors: ожидается массив")
    if len(sectors) > ITEM_LIMITS["wheel.sectors"]:
        raise ConfigInvalidError(
            f"wheel.sectors: не больше {ITEM_LIMITS['wheel.sectors']} секторов"
        )
    for sector in sectors:
        if not isinstance(sector, Mapping):
            raise ConfigInvalidError("wheel.sectors: сектор должен быть объектом")
        _int(sector, "weight", "wheel.sectors[].weight", minimum=1)
        prize = sector.get("prize")
        if isinstance(prize, Mapping) and prize.get("multiplier") is not None:
            _check_multiplier(prize["multiplier"])


def _check_multiplier(value: object) -> None:
    """Множитель буста: число строго больше 1 и не выше границы схемы."""
    maximum = BOUNDS["wheel.sectors[].prize.multiplier"]
    if isinstance(value, bool) or not isinstance(value, int | float) or not 1 < value <= maximum:
        raise ConfigInvalidError(
            f"wheel.sectors[].prize.multiplier: число больше 1, не выше {maximum}"
        )


def band_amount(bands: Sequence[Band], place: int) -> int:
    """Награда за место по сетке; места вне сетки — 0."""
    for band in bands:
        if band.first <= place <= band.last:
            return band.amount
    return 0
