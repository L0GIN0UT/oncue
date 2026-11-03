"""Статистика угадываний по контенту (ADR 0076): чистая логика без IO.

Из итогов сессии (``game.GameFinished``) и матча (``match.MatchFinished``) получаются
наблюдения «единица × режим × исход раунда», из них — инкременты агрегатов. Что считается
«показано» и «угадано», определяет исход раунда из события; пороги и размеры корзин —
``StatsRules`` (настройки сервиса), в код ничего не зашито.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class InvalidStatsEventError(ValueError):
    """Payload события не соответствует контракту (повтор не поможет)."""


class RoundOutcome(StrEnum):
    """Исход раунда для одного игрока; ``void`` (раунд не состоялся) не считается."""

    EXACT = "exact"
    PARTIAL = "partial"
    MISS = "miss"
    GAVE_UP = "gave_up"


@dataclass(frozen=True, slots=True)
class StatsRules:
    """Правила подсчёта (из настроек).

    Attributes:
        time_bucket_ms: Ширина корзины гистограммы времени точного ответа.
        time_bucket_max: Номер последней корзины: всё, что дольше, попадает в неё.
        match_counted_finishes: Как игрок закончил матч (``players[].finish``), чтобы его
            раунды учитывались; ушедший или сдавшийся ответов в начатых раундах не дал.
        count_aborted_matches: Учитывать ли прерванные матчи.
    """

    time_bucket_ms: int
    time_bucket_max: int
    match_counted_finishes: frozenset[str]
    count_aborted_matches: bool


@dataclass(frozen=True, slots=True)
class RoundObservation:
    """Один показ единицы одному игроку."""

    media_item_id: uuid.UUID
    mode_key: str
    outcome: RoundOutcome
    level: int | None
    elapsed_ms: int | None


@dataclass(slots=True)
class StatsDelta:
    """Инкремент агрегатов пары (единица, режим)."""

    shown: int = 0
    exact: int = 0
    partial: int = 0
    miss: int = 0
    gave_up: int = 0
    time_sum_ms: int = 0
    time_count: int = 0
    level_buckets: dict[int, int] = field(default_factory=dict)
    time_buckets: dict[int, int] = field(default_factory=dict)


StatsKey = tuple[uuid.UUID, str]


def _uuid(value: Any, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise InvalidStatsEventError(f"{name}: не UUID") from exc


def _mode_key(payload: Mapping[str, Any]) -> str:
    mode = payload.get("mode_key")
    if not isinstance(mode, str) or not mode:
        raise InvalidStatsEventError("нет mode_key")
    return mode


def _int_or_none(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InvalidStatsEventError(f"{name}: ожидается неотрицательное целое или null")
    return value


def observations_from_game_finished(payload: Mapping[str, Any]) -> list[RoundObservation]:
    """Раунды соло-сессии; ``void`` пропускается.

    Raises:
        InvalidStatsEventError: нет ``mode_key``/``rounds`` или неизвестный исход.
    """
    mode = _mode_key(payload)
    rounds = payload.get("rounds")
    if not isinstance(rounds, list):
        raise InvalidStatsEventError("нет rounds")
    found: list[RoundObservation] = []
    for raw in rounds:
        if not isinstance(raw, dict):
            raise InvalidStatsEventError("раунд не объект")
        outcome = raw.get("outcome")
        if outcome == "void":
            continue
        try:
            parsed = RoundOutcome(str(outcome))
        except ValueError as exc:
            raise InvalidStatsEventError(f"неизвестный исход раунда: {outcome!r}") from exc
        found.append(
            RoundObservation(
                media_item_id=_uuid(raw.get("media_item_id"), "media_item_id"),
                mode_key=mode,
                outcome=parsed,
                level=_int_or_none(raw.get("level"), "level"),
                elapsed_ms=_int_or_none(raw.get("elapsed_ms"), "elapsed_ms"),
            )
        )
    return found


def _match_outcome(result: Mapping[str, Any]) -> RoundOutcome:
    """Частичный ответ, точный (есть уровень) или промах; сдачи в матче нет."""
    if result.get("partial") is True:
        return RoundOutcome.PARTIAL
    return RoundOutcome.EXACT if result.get("level") is not None else RoundOutcome.MISS


def observations_from_match_finished(
    payload: Mapping[str, Any], rules: StatsRules
) -> list[RoundObservation]:
    """Результаты игроков матча, закончивших его как ``match_counted_finishes``.

    Raises:
        InvalidStatsEventError: нет ``mode_key``/``players``/``rounds``.
    """
    if payload.get("aborted") is True and not rules.count_aborted_matches:
        return []
    mode = _mode_key(payload)
    players, rounds = payload.get("players"), payload.get("rounds")
    if not isinstance(players, list) or not isinstance(rounds, list):
        raise InvalidStatsEventError("нет players или rounds")
    counted = {
        str(p.get("user_id"))
        for p in players
        if isinstance(p, dict) and p.get("finish") in rules.match_counted_finishes
    }
    found: list[RoundObservation] = []
    for rnd in rounds:
        if not isinstance(rnd, dict):
            raise InvalidStatsEventError("раунд не объект")
        item = _uuid(rnd.get("media_item_id"), "media_item_id")
        results = rnd.get("results", [])
        if not isinstance(results, list):
            raise InvalidStatsEventError("results не список")
        for res in results:
            if not isinstance(res, dict):
                raise InvalidStatsEventError("результат не объект")
            if str(res.get("user_id")) not in counted:
                continue
            found.append(
                RoundObservation(
                    media_item_id=item,
                    mode_key=mode,
                    outcome=_match_outcome(res),
                    level=_int_or_none(res.get("level"), "level"),
                    elapsed_ms=_int_or_none(res.get("elapsed_ms"), "elapsed_ms"),
                )
            )
    return found


def aggregate(
    observations: Iterable[RoundObservation], rules: StatsRules
) -> dict[StatsKey, StatsDelta]:
    """Свернуть наблюдения в инкременты по ключу (единица, режим).

    Уровень и время учитываются только у точного ответа: у промаха они ничего не
    говорят о сложности, а у частичного время относится к другому ответу.
    """
    deltas: dict[StatsKey, StatsDelta] = defaultdict(StatsDelta)
    for obs in observations:
        d = deltas[(obs.media_item_id, obs.mode_key)]
        d.shown += 1
        if obs.outcome is RoundOutcome.EXACT:
            d.exact += 1
            if obs.level is not None:
                d.level_buckets[obs.level] = d.level_buckets.get(obs.level, 0) + 1
            if obs.elapsed_ms is not None:
                d.time_sum_ms += obs.elapsed_ms
                d.time_count += 1
                bucket = min(obs.elapsed_ms // rules.time_bucket_ms, rules.time_bucket_max)
                d.time_buckets[bucket] = d.time_buckets.get(bucket, 0) + 1
        elif obs.outcome is RoundOutcome.PARTIAL:
            d.partial += 1
        elif obs.outcome is RoundOutcome.MISS:
            d.miss += 1
        else:
            d.gave_up += 1
    return dict(deltas)


def approx_median_ms(buckets: Mapping[int, int], *, bucket_ms: int) -> int | None:
    """Приближённая (нижняя) медиана по гистограмме: середина корзины среднего ответа.

    Точность — полширины корзины; последняя корзина накапливает «долгие» ответы.
    """
    total = sum(buckets.values())
    if total == 0:
        return None
    rank = (total + 1) // 2
    seen = 0
    for bucket in sorted(buckets):
        seen += buckets[bucket]
        if seen >= rank:
            return bucket * bucket_ms + bucket_ms // 2
    return None  # pragma: no cover — rank не больше total
