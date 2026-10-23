"""Конфиг ladder-а (ADR 0037–0039): неизменяемая модель и проверка по ``ladder.v1.json``.

В коде нет значений параметров: старт, K, пороги званий, окна подбора, длина сезона —
всё приходит из документа версии. ``parse`` повторяет проверки JSON Schema контракта и
добавляет инварианты, которые схема выразить не может (порядок званий, покрытие форматов
множителями, часовой пояс). Тест сверяет оба набора проверок на одних и тех же поломках.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rating_service.domain.errors import LadderInvalidError

# Домены — перечисление контракта (enum в ladder.v1.json). Форматы очереди в коде не
# перечислены: набор задаёт сам ladder (`formats`), множители обязаны ему соответствовать.
DOMAINS: Final = ("music", "film")
_KEY_MAX = 64
_TITLE_KEY_MAX = 100
_TZ_MAX = 64
_PAGE_MAX = 100

# Структурные границы значений (S1): защита от переполнения int32-колонок, `10 ** x` и
# неограниченных документов. Совпадают с minimum/maximum/maxItems в ladder.v1.json.
RATING_LIMIT: Final = 1_000_000
K_MAX: Final = 1000
SCALE_MAX: Final = 100_000
MULTIPLIER_MAX: Final = 100
PENALTY_MAX: Final = 100_000
CALIBRATION_MAX: Final = 10_000
SEASON_DAYS_MAX: Final = 366
TIERS_MAX: Final = 100
FORMATS_MAX: Final = 10
WINDOW_MAX: Final = 1_000_000
PERIOD_S_MAX: Final = 86_400
INTERVAL_S_MAX: Final = 3_600
MIN_MATCHES_MAX: Final = 100_000


@dataclass(frozen=True, slots=True)
class Tier:
    """Звание: ``below`` — строгая верхняя граница, ``None`` у высшего."""

    key: str
    family: str
    step: int | None
    title_key: str
    below: int | None


@dataclass(frozen=True, slots=True)
class EloConfig:
    """Параметры Эло."""

    start: int
    k_calibrating: float
    k: float
    scale: float
    format_multiplier: Mapping[str, float]
    leaver_penalty: int


@dataclass(frozen=True, slots=True)
class CalibrationConfig:
    """Калибровка: сколько матчей и скрывается ли звание."""

    matches: int
    hide_tier: bool


@dataclass(frozen=True, slots=True)
class WindowConfig:
    """Окно подбора: растёт со временем ожидания."""

    base: int
    step: int
    step_every_s: int
    max: int


@dataclass(frozen=True, slots=True)
class MatchmakingConfig:
    """Подбор соперника (ADR 0038)."""

    window: WindowConfig
    slow_hint_after_s: int
    interval_s: int


@dataclass(frozen=True, slots=True)
class SoftReset:
    """Мягкий сброс: ``new = target + ratio × (old − target)``."""

    target: int
    ratio: float


@dataclass(frozen=True, slots=True)
class SeasonConfig:
    """Сезон: длина и сброс."""

    length_days: int
    soft_reset: SoftReset


@dataclass(frozen=True, slots=True)
class WeeklyConfig:
    """Топ недели: зона начала недели и размер."""

    timezone: str
    top_size: int


@dataclass(frozen=True, slots=True)
class LeaderboardConfig:
    """Лидерборд: порог входа и размер страницы."""

    min_matches: int
    page_size: int


class _Errors:
    """Накопитель ошибок проверки (путь JSON-указателем и сообщение)."""

    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, path: str, message: str) -> None:
        self.items.append({"path": path or "/", "message": message})


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


class _Reader:
    """Типобезопасное чтение узлов документа с накоплением ошибок."""

    def __init__(self, errors: _Errors) -> None:
        self.errors = errors

    def obj(
        self, raw: object, path: str, fields: tuple[str, ...], optional: tuple[str, ...] = ()
    ) -> Mapping[str, Any]:
        if not isinstance(raw, Mapping):
            self.errors.add(path, "ожидается объект")
            return {}
        for name in fields:
            if name not in raw:
                self.errors.add(f"{path}/{name}", "обязательное поле")
        for name in raw:
            if name not in fields and name not in optional:
                self.errors.add(f"{path}/{name}", "лишнее поле")
        return raw

    def integer(
        self, raw: object, path: str, *, minimum: int | None = None, maximum: int | None = None
    ) -> int:
        value: int | None = None
        if (
            isinstance(raw, bool)
            or not isinstance(raw, int | float)
            or (isinstance(raw, float) and not raw.is_integer())
        ):
            self.errors.add(path, "ожидается целое число")
        else:
            value = int(raw)
        if value is None:
            return 0
        if minimum is not None and value < minimum:
            self.errors.add(path, f"не меньше {minimum}")
        if maximum is not None and value > maximum:
            self.errors.add(path, f"не больше {maximum}")
        return value

    def number(
        self,
        raw: object,
        path: str,
        *,
        above: float | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float:
        if not _is_number(raw) or not math.isfinite(float(raw)):  # type: ignore[arg-type]  # тип проверен _is_number
            self.errors.add(path, "ожидается конечное число")
            return 0.0
        value = float(raw)  # type: ignore[arg-type]  # тип проверен выше
        if above is not None and value <= above:
            self.errors.add(path, f"строго больше {above:g}")
        if minimum is not None and value < minimum:
            self.errors.add(path, f"не меньше {minimum:g}")
        if maximum is not None and value > maximum:
            self.errors.add(path, f"не больше {maximum:g}")
        return value

    def boolean(self, raw: object, path: str) -> bool:
        if not isinstance(raw, bool):
            self.errors.add(path, "ожидается true/false")
            return False
        return raw

    def text(
        self,
        raw: object,
        path: str,
        *,
        max_length: int,
        allowed: tuple[str, ...] | None = None,
        charset: str = "key",
    ) -> str:
        if not isinstance(raw, str):
            self.errors.add(path, "ожидается строка")
            return ""
        if len(raw) > max_length or not raw:
            self.errors.add(path, f"длина 1..{max_length}")
        if allowed is not None and raw not in allowed:
            self.errors.add(path, f"одно из: {', '.join(allowed)}")
        elif allowed is None and charset and not _matches(raw, charset):
            self.errors.add(path, "недопустимые символы")
        return raw


def _matches(value: str, charset: str) -> bool:
    """``key``: ``^[a-z][a-z0-9_]*$``; ``i18n``: то же с точкой; пустой набор — любой."""
    extra = "." if charset == "i18n" else ""
    if not value or not ("a" <= value[0] <= "z"):
        return False
    return all(("a" <= c <= "z") or c.isdigit() or c == "_" or c in extra for c in value)


@dataclass(frozen=True, slots=True)
class LadderConfig:
    """Версия конфига ladder-а. Создаётся только через ``parse``."""

    key: str
    domain: str
    formats: tuple[str, ...]
    elo: EloConfig
    calibration: CalibrationConfig
    tiers: tuple[Tier, ...]
    matchmaking: MatchmakingConfig
    season: SeasonConfig
    weekly: WeeklyConfig
    leaderboard: LeaderboardConfig

    # ------------------------------------------------------------------ разбор
    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> LadderConfig:
        """Проверить документ и собрать конфиг.

        Raises:
            LadderInvalidError: ``details.errors`` — все найденные ошибки с путями.
        """
        errors = _Errors()
        r = _Reader(errors)
        top = r.obj(
            raw,
            "",
            (
                "key",
                "domain",
                "formats",
                "elo",
                "calibration",
                "tiers",
                "matchmaking",
                "season",
                "weekly",
                "leaderboard",
            ),
        )
        formats = _formats(top.get("formats"), r)
        config = cls(
            key=r.text(top.get("key"), "/key", max_length=_KEY_MAX),
            domain=r.text(top.get("domain"), "/domain", max_length=_KEY_MAX, allowed=DOMAINS),
            formats=formats,
            elo=_elo(top.get("elo"), formats, r),
            calibration=_calibration(top.get("calibration"), r),
            tiers=_tiers(top.get("tiers"), r),
            matchmaking=_matchmaking(top.get("matchmaking"), r),
            season=_season(top.get("season"), r),
            weekly=_weekly(top.get("weekly"), r),
            leaderboard=_leaderboard(top.get("leaderboard"), r),
        )
        if errors.items:
            raise LadderInvalidError(errors.items)
        return config

    # ------------------------------------------------------------------ документ
    def to_dict(self) -> dict[str, Any]:
        """Канонический документ (то, что хранится и отдаётся админке)."""
        w = self.matchmaking.window
        return {
            "key": self.key,
            "domain": self.domain,
            "formats": list(self.formats),
            "elo": {
                "start": self.elo.start,
                "k_calibrating": _plain(self.elo.k_calibrating),
                "k": _plain(self.elo.k),
                "scale": _plain(self.elo.scale),
                "format_multiplier": {k: _plain(v) for k, v in self.elo.format_multiplier.items()},
                "leaver_penalty": self.elo.leaver_penalty,
            },
            "calibration": {
                "matches": self.calibration.matches,
                "hide_tier": self.calibration.hide_tier,
            },
            "tiers": [
                {
                    "key": t.key,
                    "family": t.family,
                    "step": t.step,
                    "title_key": t.title_key,
                    "below": t.below,
                }
                for t in self.tiers
            ],
            "matchmaking": {
                "window": {
                    "base": w.base,
                    "step": w.step,
                    "step_every_s": w.step_every_s,
                    "max": w.max,
                },
                "slow_hint_after_s": self.matchmaking.slow_hint_after_s,
                "interval_s": self.matchmaking.interval_s,
            },
            "season": {
                "length_days": self.season.length_days,
                "soft_reset": {
                    "target": self.season.soft_reset.target,
                    "ratio": _plain(self.season.soft_reset.ratio),
                },
            },
            "weekly": {"timezone": self.weekly.timezone, "top_size": self.weekly.top_size},
            "leaderboard": {
                "min_matches": self.leaderboard.min_matches,
                "page_size": self.leaderboard.page_size,
            },
        }

    # ------------------------------------------------------------------ звания, калибровка
    def tier_for(self, rating: int) -> Tier:
        """Звание по рейтингу: первое, чья верхняя граница выше рейтинга."""
        for tier in self.tiers:
            if tier.below is None or rating < tier.below:
                return tier
        return self.tiers[-1]

    def next_tier_at(self, rating: int) -> int | None:
        """С какого рейтинга начинается следующее звание; ``None`` — звание высшее."""
        return self.tier_for(rating).below

    def is_calibrating(self, matches_total: int) -> bool:
        """Идёт ли калибровка у игрока с таким числом рейтинговых матчей."""
        return matches_total < self.calibration.matches

    def calibration_left(self, matches_total: int) -> int:
        """Сколько матчей осталось до конца калибровки."""
        return max(0, self.calibration.matches - matches_total)


def _plain(value: float) -> int | float:
    """``20.0`` → ``20``: документ остаётся таким же, каким его прислали."""
    return int(value) if float(value).is_integer() else value


def _formats(raw: object, r: _Reader) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        r.errors.add("/formats", "ожидается непустой список")
        return ()
    if len(raw) > FORMATS_MAX:
        r.errors.add("/formats", f"не больше {FORMATS_MAX} форматов")
        return ()
    result: list[str] = []
    for i, item in enumerate(raw):
        value = r.text(item, f"/formats/{i}", max_length=_KEY_MAX)
        if value in result:
            r.errors.add(f"/formats/{i}", "повтор формата")
        result.append(value)
    return tuple(result)


def _elo(raw: object, formats: tuple[str, ...], r: _Reader) -> EloConfig:
    o = r.obj(
        raw,
        "/elo",
        ("start", "k_calibrating", "k", "scale", "format_multiplier", "leaver_penalty"),
    )
    multipliers: dict[str, float] = {}
    raw_mult = o.get("format_multiplier")
    if isinstance(raw_mult, Mapping):
        for name, value in raw_mult.items():
            path = f"/elo/format_multiplier/{name}"
            if name not in formats:
                r.errors.add(path, "множитель для формата, которого нет в `formats` ladder-а")
                continue
            multipliers[name] = r.number(value, path, above=0, maximum=MULTIPLIER_MAX)
        for fmt in formats:
            if fmt not in multipliers:
                r.errors.add(f"/elo/format_multiplier/{fmt}", "нет множителя для формата ladder-а")
    elif "format_multiplier" in o:
        r.errors.add("/elo/format_multiplier", "ожидается объект")
    return EloConfig(
        start=r.integer(o.get("start"), "/elo/start", minimum=-RATING_LIMIT, maximum=RATING_LIMIT),
        k_calibrating=r.number(
            o.get("k_calibrating"), "/elo/k_calibrating", minimum=1, maximum=K_MAX
        ),
        k=r.number(o.get("k"), "/elo/k", minimum=1, maximum=K_MAX),
        scale=r.number(o.get("scale"), "/elo/scale", minimum=1, maximum=SCALE_MAX),
        format_multiplier=multipliers,
        leaver_penalty=r.integer(
            o.get("leaver_penalty"), "/elo/leaver_penalty", minimum=0, maximum=PENALTY_MAX
        ),
    )


def _calibration(raw: object, r: _Reader) -> CalibrationConfig:
    o = r.obj(raw, "/calibration", ("matches", "hide_tier"))
    return CalibrationConfig(
        matches=r.integer(
            o.get("matches"), "/calibration/matches", minimum=0, maximum=CALIBRATION_MAX
        ),
        hide_tier=r.boolean(o.get("hide_tier"), "/calibration/hide_tier"),
    )


def _tiers(raw: object, r: _Reader) -> tuple[Tier, ...]:
    if not isinstance(raw, list) or not raw:
        r.errors.add("/tiers", "ожидается непустой список")
        return ()
    if len(raw) > TIERS_MAX:
        r.errors.add("/tiers", f"не больше {TIERS_MAX} званий")
        return ()
    tiers: list[Tier] = []
    seen: set[str] = set()
    previous: int | None = None
    last = len(raw) - 1
    for i, item in enumerate(raw):
        base = f"/tiers/{i}"
        o = r.obj(item, base, ("key", "family", "step", "title_key", "below"))
        key = r.text(o.get("key"), f"{base}/key", max_length=_KEY_MAX)
        if key in seen:
            r.errors.add(f"{base}/key", "ключ звания повторяется")
        seen.add(key)
        step_raw = o.get("step")
        step = None if step_raw is None else r.integer(step_raw, f"{base}/step", minimum=1)
        below_raw = o.get("below")
        below: int | None = None
        if below_raw is None:
            if i != last and "below" in o:
                r.errors.add(f"{base}/below", "открытая граница допустима только у высшего звания")
        else:
            below = r.integer(
                below_raw, f"{base}/below", minimum=-RATING_LIMIT, maximum=RATING_LIMIT
            )
            if i == last:
                r.errors.add(f"{base}/below", "у высшего звания граница должна быть null")
            if previous is not None and below <= previous:
                r.errors.add(f"{base}/below", "границы должны строго возрастать")
            previous = below
        tiers.append(
            Tier(
                key=key,
                family=r.text(o.get("family"), f"{base}/family", max_length=_KEY_MAX),
                step=step,
                title_key=r.text(
                    o.get("title_key"),
                    f"{base}/title_key",
                    max_length=_TITLE_KEY_MAX,
                    charset="i18n",
                ),
                below=below,
            )
        )
    return tuple(tiers)


def _matchmaking(raw: object, r: _Reader) -> MatchmakingConfig:
    o = r.obj(raw, "/matchmaking", ("window", "slow_hint_after_s", "interval_s"))
    w = r.obj(o.get("window"), "/matchmaking/window", ("base", "step", "step_every_s", "max"))
    window = WindowConfig(
        base=r.integer(w.get("base"), "/matchmaking/window/base", minimum=1, maximum=WINDOW_MAX),
        step=r.integer(w.get("step"), "/matchmaking/window/step", minimum=1, maximum=WINDOW_MAX),
        step_every_s=r.integer(
            w.get("step_every_s"),
            "/matchmaking/window/step_every_s",
            minimum=1,
            maximum=PERIOD_S_MAX,
        ),
        max=r.integer(w.get("max"), "/matchmaking/window/max", minimum=1, maximum=WINDOW_MAX),
    )
    if window.max < window.base:
        r.errors.add("/matchmaking/window/max", "потолок окна не меньше начального")
    return MatchmakingConfig(
        window=window,
        slow_hint_after_s=r.integer(
            o.get("slow_hint_after_s"),
            "/matchmaking/slow_hint_after_s",
            minimum=1,
            maximum=PERIOD_S_MAX,
        ),
        interval_s=r.integer(
            o.get("interval_s"), "/matchmaking/interval_s", minimum=1, maximum=INTERVAL_S_MAX
        ),
    )


def _season(raw: object, r: _Reader) -> SeasonConfig:
    o = r.obj(raw, "/season", ("length_days", "soft_reset"))
    s = r.obj(o.get("soft_reset"), "/season/soft_reset", ("target", "ratio"))
    return SeasonConfig(
        length_days=r.integer(
            o.get("length_days"), "/season/length_days", minimum=1, maximum=SEASON_DAYS_MAX
        ),
        soft_reset=SoftReset(
            target=r.integer(
                s.get("target"), "/season/soft_reset/target", minimum=0, maximum=RATING_LIMIT
            ),
            ratio=r.number(s.get("ratio"), "/season/soft_reset/ratio", minimum=0, maximum=1),
        ),
    )


def _weekly(raw: object, r: _Reader) -> WeeklyConfig:
    o = r.obj(raw, "/weekly", ("timezone", "top_size"))
    zone = r.text(o.get("timezone"), "/weekly/timezone", max_length=_TZ_MAX, charset="")
    if zone:
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            r.errors.add("/weekly/timezone", "неизвестная зона IANA")
    return WeeklyConfig(
        timezone=zone,
        top_size=r.integer(o.get("top_size"), "/weekly/top_size", minimum=1, maximum=_PAGE_MAX),
    )


def _leaderboard(raw: object, r: _Reader) -> LeaderboardConfig:
    o = r.obj(raw, "/leaderboard", ("min_matches", "page_size"))
    return LeaderboardConfig(
        min_matches=r.integer(
            o.get("min_matches"), "/leaderboard/min_matches", minimum=0, maximum=MIN_MATCHES_MAX
        ),
        page_size=r.integer(
            o.get("page_size"), "/leaderboard/page_size", minimum=1, maximum=_PAGE_MAX
        ),
    )
