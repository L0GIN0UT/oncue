"""Конфиг progression на одну версию и его семантическая проверка (ADR 0042–0044).

Схема — ``contracts/progression/progression_config.v1.json``. Здесь разбирается то, что нужно
начислению (P1): уровни, опыт, Версы, сетки мест, часовой пояс. Остальные блоки (задания,
бейджи, колесо, лимиты выдачи) хранятся как есть в ``body`` и разбираются своими потоками;
общий валидатор проверяет лишь то, что опасно для всех (веса колеса > 0).

В коде значений нет: любая сумма, порог и граница приходят из версии конфига.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gv_core.errors import InvalidInputError
from progression_service.domain import metrics

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
    "verses.online_daily_cap": _AMOUNT_MAX,
    "verses.solo_daily_cap": _AMOUNT_MAX,
    "verses.weekly_top[].from": _AMOUNT_MAX,
    "verses.weekly_top[].to": _AMOUNT_MAX,
    "verses.weekly_top[].amount": _AMOUNT_MAX,
    "verses.season[].from": _AMOUNT_MAX,
    "verses.season[].to": _AMOUNT_MAX,
    "verses.season[].amount": _AMOUNT_MAX,
    "wheel.sectors[].weight": 1_000_000,
    "wheel.sectors[].prize.multiplier": 10,
    "wheel.sectors[].prize.amount": _AMOUNT_MAX,
    "wheel.sectors[].prize.matches": 1000,
    "wheel.max_boost_queue": 100,
    "quests.daily_count": 20,
    "quests.weekly_count": 50,
    "quests.items[].weight": 1000,
    "quests.items[].target": 1_000_000,
    "quests.items[].reward.xp": _AMOUNT_MAX,
    "quests.items[].reward.verses": _AMOUNT_MAX,
    "badges[].target": 1_000_000,
    "shop.media.static_max_bytes": 20_000_000,
    "shop.media.animated_max_bytes": 20_000_000,
    "shop.media.animated_max_frames": 1000,
    "shop.media.animated_max_duration_ms": 60_000,
    "shop.media.max_side_px": 4096,
    "shop.rarities[].duplicate_verses": _AMOUNT_MAX,
    "promo.attempts_per_user": 10_000,
    "promo.attempts_per_ip": 10_000,
    "promo.window_s": 86_400,
    "promo.code_length": 32,
    "promo.max_batch_verses": 1_000_000_000,
    "promo.max_daily_verses_per_actor": 1_000_000_000,
    "promo.max_daily_verses_total": 1_000_000_000,
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
    "quests.items": 500,
    "badges": 500,
    "shop.slots": 16,
    "shop.rarities": 16,
    "spend.bucket_order": 2,
}
"""Верхние границы длины массивов (``maxItems`` схемы)."""

MINIMUMS: Mapping[str, int] = {
    "shop.media.animated_max_frames": 2,
    "shop.media.max_side_px": 16,
    "promo.code_length": 8,
}
"""Нижние границы (``minimum`` схемы), отличные от значения по умолчанию (0 для сумм, 1 для
размеров и сроков - см. ``_sized``)."""

SPEND_BUCKETS = ("earned", "paid")
"""Бакеты, которые знает ``spend.bucket_order`` (``items.enum`` схемы)."""

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
    """Источники Версов, потолки (соло, онлайн) и сетки наград рейтинга."""

    solo_round: int
    daily: int
    duel: DuelVerses
    party_by_place: tuple[int, ...]
    solo_daily_cap: int
    online_daily_cap: int | None
    weekly_top: tuple[Band, ...]
    season: tuple[Band, ...]


@dataclass(frozen=True, slots=True)
class Reward:
    """Награда задания: опыт и Версы (0 — нет)."""

    xp: int
    verses: int


@dataclass(frozen=True, slots=True)
class QuestDef:
    """Задание пула.

    ``weight`` - вес при случайной выдаче; ``domain`` - ограничение (``None`` - любой).
    """

    key: str
    period: str
    title: str
    description: str
    metric: str
    target: int
    reward: Reward
    enabled: bool
    weight: int
    domain: str | None


@dataclass(frozen=True, slots=True)
class QuestsConfig:
    """Задания: размер назначения и пул."""

    daily_count: int
    weekly_count: int
    items: tuple[QuestDef, ...]

    def pool(self, period: str) -> tuple[QuestDef, ...]:
        """Включённые задания пула периода в порядке конфига."""
        return tuple(q for q in self.items if q.enabled and q.period == period)


@dataclass(frozen=True, slots=True)
class BadgeDef:
    """Бейдж: выдаётся один раз, когда метрика достигла ``target``."""

    key: str
    title: str
    description: str
    metric: str
    target: int
    enabled: bool


class PrizeKind(StrEnum):
    """Вид приза колеса (значения - как в схеме конфига и API)."""

    XP = "xp"
    VERSES = "verses"
    BOOST = "boost"
    ITEM = "item"
    NONE = "none"


PAID_KINDS = frozenset({PrizeKind.XP, PrizeKind.VERSES})
"""Призы с суммой: ``amount`` больше нуля. У остальных видов ``amount`` равен нулю."""


@dataclass(frozen=True, slots=True)
class PrizeDef:
    """Приз сектора."""

    kind: PrizeKind
    amount: int
    multiplier: Decimal | None
    matches: int | None
    item_key: str | None


@dataclass(frozen=True, slots=True)
class SectorDef:
    """Сектор колеса."""

    key: str
    title: str
    weight: int
    prize: PrizeDef


@dataclass(frozen=True, slots=True)
class WheelConfig:
    """Колесо: включено ли, секторы, длина очереди бустов."""

    enabled: bool
    sectors: tuple[SectorDef, ...]
    max_boost_queue: int


@dataclass(frozen=True, slots=True)
class SlotDef:
    """Слот оформления профиля (аватар, рамка, титул, фон)."""

    key: str
    title: str


@dataclass(frozen=True, slots=True)
class RarityDef:
    """Редкость предмета; ``duplicate_verses`` - Версы за дубль, выпавший с колеса."""

    key: str
    title: str
    duplicate_verses: int


@dataclass(frozen=True, slots=True)
class MediaLimits:
    """Лимиты картинок и анимаций предметов (ADR 0046)."""

    static_max_bytes: int
    animated_max_bytes: int
    animated_max_frames: int
    animated_max_duration_ms: int
    max_side_px: int


@dataclass(frozen=True, slots=True)
class ShopConfig:
    """Магазин и инвентарь (ADR 0045): слоты, редкости, лимиты медиа."""

    slots: tuple[SlotDef, ...]
    rarities: tuple[RarityDef, ...]
    media: MediaLimits
    custom_avatar_item_key: str | None

    def slot(self, key: str) -> SlotDef | None:
        """Слот по ключу."""
        return next((s for s in self.slots if s.key == key), None)

    def rarity(self, key: str) -> RarityDef | None:
        """Редкость по ключу."""
        return next((r for r in self.rarities if r.key == key), None)


@dataclass(frozen=True, slots=True)
class PromoConfig:
    """Промокоды (ADR 0048): лимиты попыток, длина кода, потолки эмиссии.

    Потолки: ``max_batch_verses`` - одна партия; ``max_daily_verses_per_actor`` - сутки на
    администратора; ``max_daily_verses_total`` - сутки на всех. Порядок: партия <= актор <= всего.
    """

    attempts_per_user: int
    attempts_per_ip: int
    window_s: int
    code_length: int
    max_batch_verses: int
    max_daily_verses_per_actor: int
    max_daily_verses_total: int


@dataclass(frozen=True, slots=True)
class SpendConfig:
    """Траты Версов (ADR 0043): порядок списания бакетов."""

    bucket_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProgressionConfig:
    """Разобранная и проверенная версия конфига.

    Attributes:
        timezone: IANA-зона суток (день события — по ``finished_at`` в этой зоне).
        levels: Кривая уровней.
        min_rounds_ratio: Доля сыгранных раундов для бонусов за результат (антиабьюз).
        xp: Опыт.
        verses: Версы.
        quests: Задания (пул и размер назначения).
        badges: Бейджи.
        wheel: Колесо.
        body: Исходное тело версии (для блоков, которые разбирают другие потоки).
        shop: Магазин 5b; ``None`` - секции нет, магазин пуст.
        promo: Промокоды 5b; ``None`` - секции нет.
        spend: Порядок трат 5b; ``None`` - секции нет (тратится только ``earned``).
    """

    timezone: str
    levels: LevelsConfig
    min_rounds_ratio: float
    xp: XpConfig
    verses: VersesConfig
    quests: QuestsConfig
    badges: tuple[BadgeDef, ...]
    wheel: WheelConfig
    body: Mapping[str, Any]
    shop: ShopConfig | None = None
    promo: PromoConfig | None = None
    spend: SpendConfig | None = None

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
        quests = _quests(_obj(body, "quests"))
        badges = _badges(body.get("badges"))
        wheel = _wheel(_obj(body, "wheel"))
        shop = _shop(_obj(body, "shop")) if "shop" in body else None
        promo = _promo(_obj(body, "promo")) if "promo" in body else None
        spend = _spend(_obj(body, "spend")) if "spend" in body else None
        return cls(
            timezone=timezone,
            levels=levels,
            min_rounds_ratio=float(ratio),
            xp=xp,
            verses=verses,
            quests=quests,
            badges=badges,
            wheel=wheel,
            body=dict(body),
            shop=shop,
            promo=promo,
            spend=spend,
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


def _optional_int(parent: Mapping[str, Any], key: str, path: str) -> int | None:
    """Необязательное целое поле: нет ключа - `None` (нет ограничения)."""
    if key not in parent:
        return None
    return _int(parent, key, path)


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
        online_daily_cap=_optional_int(raw, "online_daily_cap", "verses.online_daily_cap"),
        weekly_top=_bands(raw, "weekly_top"),
        season=_bands(raw, "season"),
    )


_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_PERIODS = ("daily", "weekly", "one_time")
_MIN_SECTORS = 2
_KEY_MAX = 64


def _key(parent: Mapping[str, Any], where: str) -> str:
    value = parent.get("key")
    if not isinstance(value, str) or len(value) > _KEY_MAX or not _KEY.match(value):
        raise ConfigInvalidError(f"{where}.key: ключ вида ^[a-z][a-z0-9_]*$ до 64 символов")
    return value


def _text(parent: Mapping[str, Any], name: str, where: str, *, limit: int, required: bool) -> str:
    value = parent.get(name, None if required else "")
    if not isinstance(value, str) or len(value) > limit or (required and not value):
        raise ConfigInvalidError(f"{where}.{name}: строка до {limit} символов")
    return value


def _flag(parent: Mapping[str, Any], name: str, where: str) -> bool:
    value = parent.get(name)
    if not isinstance(value, bool):
        raise ConfigInvalidError(f"{where}.{name}: ожидается true/false")
    return value


def _metric(parent: Mapping[str, Any], where: str) -> str:
    value = parent.get("metric")
    if not isinstance(value, str) or not metrics.is_known(value):
        raise ConfigInvalidError(f"{where}.metric: неизвестная метрика {value!r}")
    return value


def _array(parent: Mapping[str, Any], name: str, path: str) -> Sequence[Any]:
    value = parent.get(name)
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ConfigInvalidError(f"{path}: ожидается массив")
    if len(value) > ITEM_LIMITS[path]:
        raise ConfigInvalidError(f"{path}: не больше {ITEM_LIMITS[path]} элементов")
    return value


def _quest(item: Mapping[str, Any]) -> QuestDef:
    key = _key(item, "quests.items[]")
    where = f"quests.items[{key}]"
    period = item.get("period")
    if period not in _PERIODS:
        raise ConfigInvalidError(f"{where}.period: daily, weekly или one_time")
    domain = item.get("domain")
    if domain not in ("music", "film", None):
        raise ConfigInvalidError(f"{where}.domain: music, film или null")
    reward = _obj(item, "reward")
    return QuestDef(
        key=key,
        period=str(period),
        title=_text(item, "title", where, limit=120, required=True),
        description=_text(item, "description", where, limit=400, required=False),
        metric=_metric(item, where),
        target=_int(item, "target", "quests.items[].target", minimum=1),
        reward=Reward(
            xp=_int(reward, "xp", "quests.items[].reward.xp"),
            verses=_int(reward, "verses", "quests.items[].reward.verses"),
        ),
        enabled=_flag(item, "enabled", where),
        weight=(
            _int(item, "weight", "quests.items[].weight", minimum=1) if "weight" in item else 1
        ),
        domain=domain,
    )


def _quests(raw: Mapping[str, Any]) -> QuestsConfig:
    items: list[QuestDef] = []
    for item in _array(raw, "items", "quests.items"):
        if not isinstance(item, Mapping):
            raise ConfigInvalidError("quests.items: задание должно быть объектом")
        quest = _quest(item)
        if any(q.key == quest.key for q in items):
            raise ConfigInvalidError(f"quests.items: ключ {quest.key!r} повторяется")
        items.append(quest)
    # Пул меньше размера назначения - не ошибка: игроку достанется весь пул (пример контракта
    # просит 10 недельных при одном задании в пуле). Выбор берёт min(count, len(pool)).
    return QuestsConfig(
        daily_count=_int(raw, "daily_count", "quests.daily_count"),
        weekly_count=_int(raw, "weekly_count", "quests.weekly_count"),
        items=tuple(items),
    )


def _badges(raw: object) -> tuple[BadgeDef, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise ConfigInvalidError("badges: ожидается массив")
    if len(raw) > ITEM_LIMITS["badges"]:
        raise ConfigInvalidError(f"badges: не больше {ITEM_LIMITS['badges']} бейджей")
    out: list[BadgeDef] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ConfigInvalidError("badges: бейдж должен быть объектом")
        key = _key(item, "badges[]")
        if any(b.key == key for b in out):
            raise ConfigInvalidError(f"badges: ключ {key!r} повторяется")
        where = f"badges[{key}]"
        out.append(
            BadgeDef(
                key=key,
                title=_text(item, "title", where, limit=120, required=True),
                description=_text(item, "description", where, limit=400, required=False),
                metric=_metric(item, where),
                target=_int(item, "target", "badges[].target", minimum=1),
                enabled=_flag(item, "enabled", where),
            )
        )
    return tuple(out)


def _prize(raw: Mapping[str, Any], where: str) -> PrizeDef:
    """Приз: поля согласованы с видом (лишнее для вида должно быть пустым)."""
    try:
        kind = PrizeKind(str(raw.get("kind")))
    except ValueError:
        known = ", ".join(k.value for k in PrizeKind)
        raise ConfigInvalidError(f"{where}.kind: один из {known}") from None
    amount = _int(raw, "amount", "wheel.sectors[].prize.amount")
    multiplier = raw.get("multiplier")
    matches = raw.get("matches")
    item_key = raw.get("item_key")
    if kind in PAID_KINDS and amount < 1:
        raise ConfigInvalidError(f"{where}: у {kind.value} amount должен быть больше 0")
    if kind not in PAID_KINDS and amount != 0:
        raise ConfigInvalidError(f"{where}: у {kind.value} amount должен быть 0")
    if kind is PrizeKind.BOOST:
        _check_multiplier(multiplier)
        if (
            isinstance(matches, bool)
            or not isinstance(matches, int)
            or not 1 <= matches <= BOUNDS["wheel.sectors[].prize.matches"]
        ):
            raise ConfigInvalidError(f"{where}.matches: целое от 1 у boost")
    elif multiplier is not None or matches is not None:
        raise ConfigInvalidError(f"{where}: multiplier и matches - только у boost")
    if kind is PrizeKind.ITEM:
        if not isinstance(item_key, str) or len(item_key) > _KEY_MAX or not _KEY.match(item_key):
            raise ConfigInvalidError(f"{where}.item_key: ключ предмета у item")
    elif item_key is not None:
        raise ConfigInvalidError(f"{where}: item_key - только у item")
    return PrizeDef(
        kind=kind,
        amount=amount,
        multiplier=None if multiplier is None else Decimal(str(multiplier)),
        matches=matches if kind is PrizeKind.BOOST else None,
        item_key=item_key if kind is PrizeKind.ITEM else None,
    )


def _wheel(raw: Mapping[str, Any]) -> WheelConfig:
    """Колесо: ≥ 2 секторов, уникальные ключи, веса > 0, призы согласованы, очередь бустов."""
    sectors: list[SectorDef] = []
    for sector in _array(raw, "sectors", "wheel.sectors"):
        if not isinstance(sector, Mapping):
            raise ConfigInvalidError("wheel.sectors: сектор должен быть объектом")
        key = _key(sector, "wheel.sectors[]")
        if any(s.key == key for s in sectors):
            raise ConfigInvalidError(f"wheel.sectors: ключ {key!r} повторяется")
        where = f"wheel.sectors[{key}]"
        sectors.append(
            SectorDef(
                key=key,
                title=_text(sector, "title", where, limit=80, required=True),
                weight=_int(sector, "weight", "wheel.sectors[].weight", minimum=1),
                prize=_prize(_obj(sector, "prize"), f"{where}.prize"),
            )
        )
    if len(sectors) < _MIN_SECTORS:
        raise ConfigInvalidError("wheel.sectors: нужно не меньше двух секторов")
    return WheelConfig(
        enabled=_flag(raw, "enabled", "wheel"),
        sectors=tuple(sectors),
        max_boost_queue=_int(raw, "max_boost_queue", "wheel.max_boost_queue", minimum=1),
    )


def _titled_keys(raw: Mapping[str, Any], name: str, path: str) -> list[Mapping[str, Any]]:
    """Непустой массив объектов с уникальными ключами (слоты, редкости)."""
    items: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for item in _array(raw, name, path):
        if not isinstance(item, Mapping):
            raise ConfigInvalidError(f"{path}: элемент должен быть объектом")
        key = _key(item, f"{path}[]")
        if key in seen:
            raise ConfigInvalidError(f"{path}: ключ {key!r} повторяется")
        seen.add(key)
        items.append(item)
    if not items:
        raise ConfigInvalidError(f"{path}: нужен хотя бы один элемент")
    return items


def _shop(raw: Mapping[str, Any]) -> ShopConfig:
    """Магазин: слоты и редкости с уникальными ключами, лимиты медиа, ключ товара-аватарки."""
    slots = tuple(
        SlotDef(
            key=_key(s, "shop.slots[]"),
            title=_text(s, "title", "shop.slots[]", limit=80, required=True),
        )
        for s in _titled_keys(raw, "slots", "shop.slots")
    )
    rarities = tuple(
        RarityDef(
            key=_key(r, "shop.rarities[]"),
            title=_text(r, "title", "shop.rarities[]", limit=80, required=True),
            duplicate_verses=_int(
                r, "duplicate_verses", "shop.rarities[].duplicate_verses", minimum=0
            ),
        )
        for r in _titled_keys(raw, "rarities", "shop.rarities")
    )
    media = _obj(raw, "media")
    limits = MediaLimits(
        static_max_bytes=_sized(media, "static_max_bytes", "shop.media.static_max_bytes"),
        animated_max_bytes=_sized(media, "animated_max_bytes", "shop.media.animated_max_bytes"),
        animated_max_frames=_sized(media, "animated_max_frames", "shop.media.animated_max_frames"),
        animated_max_duration_ms=_sized(
            media, "animated_max_duration_ms", "shop.media.animated_max_duration_ms"
        ),
        max_side_px=_sized(media, "max_side_px", "shop.media.max_side_px"),
    )
    if "custom_avatar_item_key" not in raw:
        raise ConfigInvalidError("shop.custom_avatar_item_key: поле обязательно (может быть null)")
    avatar = raw["custom_avatar_item_key"]
    if avatar is not None and (
        not isinstance(avatar, str) or len(avatar) > _KEY_MAX or not _KEY.match(avatar)
    ):
        raise ConfigInvalidError("shop.custom_avatar_item_key: ключ предмета или null")
    return ShopConfig(slots=slots, rarities=rarities, media=limits, custom_avatar_item_key=avatar)


def _sized(parent: Mapping[str, Any], key: str, path: str) -> int:
    """Размер или срок: целое от ``MINIMUMS[path]`` (по умолчанию 1) до ``BOUNDS[path]``."""
    return _int(parent, key, path, minimum=MINIMUMS.get(path, 1))


def _promo(raw: Mapping[str, Any]) -> PromoConfig:
    cfg = PromoConfig(
        attempts_per_user=_sized(raw, "attempts_per_user", "promo.attempts_per_user"),
        attempts_per_ip=_sized(raw, "attempts_per_ip", "promo.attempts_per_ip"),
        window_s=_sized(raw, "window_s", "promo.window_s"),
        code_length=_sized(raw, "code_length", "promo.code_length"),
        max_batch_verses=_sized(raw, "max_batch_verses", "promo.max_batch_verses"),
        max_daily_verses_per_actor=_sized(
            raw, "max_daily_verses_per_actor", "promo.max_daily_verses_per_actor"
        ),
        max_daily_verses_total=_sized(
            raw, "max_daily_verses_total", "promo.max_daily_verses_total"
        ),
    )
    # Схема порядок потолков выразить не может - его держит только домен.
    if not cfg.max_batch_verses <= cfg.max_daily_verses_per_actor <= cfg.max_daily_verses_total:
        raise ConfigInvalidError(
            "promo: нужно max_batch_verses <= max_daily_verses_per_actor <= max_daily_verses_total"
        )
    return cfg


def _spend(raw: Mapping[str, Any]) -> SpendConfig:
    """Порядок трат: уникальные известные бакеты, ``earned`` обязан быть (ADR 0043)."""
    order = _array(raw, "bucket_order", "spend.bucket_order")
    if not order:
        raise ConfigInvalidError("spend.bucket_order: нужен хотя бы один бакет")
    for bucket in order:
        if bucket not in SPEND_BUCKETS:
            raise ConfigInvalidError(f"spend.bucket_order: неизвестный бакет {bucket!r}")
    if len(set(order)) != len(order):
        raise ConfigInvalidError("spend.bucket_order: бакеты не должны повторяться")
    if "earned" not in order:
        raise ConfigInvalidError("spend.bucket_order: нужен бакет earned")
    return SpendConfig(bucket_order=tuple(order))


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
