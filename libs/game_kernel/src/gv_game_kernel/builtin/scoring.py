"""Встроенные способы подсчёта очков: ``by_level`` и ``by_speed`` (D10).

Дробные коэффициенты из конфига (``partial_ratio``, ``window_s``) приходят как float;
считаем через ``Fraction(str(x))``, чтобы ``floor(100 × 0.29)`` давал 29, а не 28 из-за
двоичного представления. Результат — всегда целое.
"""

# ruff: noqa: ARG002 — методы реализуют протоколы плагинов, не всем нужны все аргументы

from __future__ import annotations

import math
from fractions import Fraction
from typing import Annotated, ClassVar

from pydantic import BaseModel, Field, StrictFloat, StrictInt

from gv_game_kernel.builtin._base import PluginParams, typed
from gv_game_kernel.plugins import ParamIssue
from gv_game_kernel.round import RoundOutcome, RoundResult
from gv_game_kernel.spec import ModeConfig

LEVELS_MISMATCH = "config.levels_mismatch"
"""Код проблемы: число очков уровней не равно числу уровней загадки."""

POINTS_INCREASING = "config.points_increasing"
"""Код проблемы: очки растут с уровнем (поздний ответ не может стоить больше раннего)."""

MIN_ABOVE_MAX = "config.min_above_max"
"""Код проблемы: ``min_points`` больше ``max_points``."""

SCORE_PRESENTATION = "score"
"""Подача итога суммой очков (``Scorer.presentation``)."""

Ratio = Annotated[StrictFloat, Field(ge=0, le=1)]


def _exact(value: float) -> Fraction:
    return Fraction(str(value))


def _floor_share(points: int, ratio: float) -> int:
    """``floor(points × ratio)`` без ошибок двоичной арифметики."""
    return math.floor(points * _exact(ratio))


# ----------------------------------------------------------------------------- by_level
class ByLevelParams(PluginParams):
    """Параметры ``by_level`` (``contracts/modes/params/scorer.by_level.v1.json``)."""

    points: Annotated[
        tuple[Annotated[StrictInt, Field(ge=0, le=1000)], ...], Field(min_length=1, max_length=20)
    ]
    partial_ratio: Ratio


class ByLevelScorer:
    """Очки по уровню угадывания.

    Точный ответ на уровне ``L`` — ``points[L-1]``. Частичный ответ (засчитывается один
    раз, на уровне ``P``) — ``floor(points[P-1] × partial_ratio)``. Итог раунда —
    ``max(exact, partial)``: частичный ответ не суммируется с точным.
    """

    key: ClassVar[str] = "by_level"
    params_schema: ClassVar[type[BaseModel]] = ByLevelParams
    presentation: ClassVar[str] = SCORE_PRESENTATION
    requires_early_end: ClassVar[bool] = False

    def score(self, outcome: RoundOutcome, params: BaseModel) -> int:
        """Очки за раунд (``void``, ``miss`` и ``gave_up`` без частичного — 0).

        Raises:
            ValueError: уровень итога вне ``points``.
        """
        p = typed(params, ByLevelParams)
        if outcome.result is RoundResult.VOID:
            return 0
        exact = 0
        if outcome.result is RoundResult.EXACT and outcome.level is not None:
            exact = self._points(p, outcome.level)
        partial = 0
        if outcome.partial_level is not None:
            partial = _floor_share(self._points(p, outcome.partial_level), p.partial_ratio)
        return max(exact, partial)

    @staticmethod
    def _points(p: ByLevelParams, level: int) -> int:
        if not 1 <= level <= len(p.points):
            raise ValueError(f"уровень {level} вне таблицы очков ({len(p.points)})")
        return p.points[level - 1]

    def max_score(self, params: BaseModel, levels_count: int) -> int:
        """Максимум за раунд — очки первого (самого дорогого) уровня."""
        return max(typed(params, ByLevelParams).points)

    def cross_check(
        self, params: BaseModel, *, config: ModeConfig, levels_count: int
    ) -> list[ParamIssue]:
        """``len(points) == levels_count``; очки не растут с уровнем."""
        p = typed(params, ByLevelParams)
        issues: list[ParamIssue] = []
        if len(p.points) != levels_count:
            issues.append(ParamIssue("points", LEVELS_MISMATCH))
        issues.extend(
            ParamIssue(f"points[{i}]", POINTS_INCREASING)
            for i in range(1, len(p.points))
            if p.points[i] > p.points[i - 1]
        )
        return issues


# ----------------------------------------------------------------------------- by_speed
class BySpeedParams(PluginParams):
    """Параметры ``by_speed`` (``contracts/modes/params/scorer.by_speed.v1.json``)."""

    max_points: Annotated[StrictInt, Field(ge=1, le=10000)]
    min_points: Annotated[StrictInt, Field(ge=0, le=10000)]
    window_s: Annotated[StrictFloat, Field(gt=0, le=3600)]
    partial_ratio: Ratio


class BySpeedScorer:
    """Очки по скорости: ``max(min_points, round(max_points × clamp(1 − t/window_s)))``.

    ``t`` — ``RoundOutcome.elapsed_ms`` (сервер считает от выдачи первого ассета раунда).
    Без времени начисляется ``min_points``: клиенту не на что влиять. Округление —
    половина вверх. Частичный ответ — ``floor(очки × partial_ratio)``, итог —
    ``max(exact, partial)``.
    """

    key: ClassVar[str] = "by_speed"
    presentation: ClassVar[str] = SCORE_PRESENTATION
    params_schema: ClassVar[type[BaseModel]] = BySpeedParams
    requires_early_end: ClassVar[bool] = False

    def score(self, outcome: RoundOutcome, params: BaseModel) -> int:
        """Очки за раунд (``void``, ``miss`` и ``gave_up`` без частичного — 0)."""
        p = typed(params, BySpeedParams)
        if outcome.result is RoundResult.VOID:
            return 0
        base = self._base(p, outcome.elapsed_ms)
        exact = base if outcome.result is RoundResult.EXACT else 0
        partial = _floor_share(base, p.partial_ratio) if outcome.partial_level is not None else 0
        return max(exact, partial)

    @staticmethod
    def _base(p: BySpeedParams, elapsed_ms: int | None) -> int:
        if elapsed_ms is None:
            return p.min_points
        share = 1 - Fraction(elapsed_ms, 1000) / _exact(p.window_s)
        share = min(Fraction(1), max(Fraction(0), share))
        return max(p.min_points, math.floor(p.max_points * share + Fraction(1, 2)))

    def max_score(self, params: BaseModel, levels_count: int) -> int:
        """Максимум за раунд — ``max_points`` (с учётом ``min_points``)."""
        p = typed(params, BySpeedParams)
        return max(p.max_points, p.min_points)

    def cross_check(
        self, params: BaseModel, *, config: ModeConfig, levels_count: int
    ) -> list[ParamIssue]:
        """``min_points ≤ max_points``."""
        p = typed(params, BySpeedParams)
        if p.min_points > p.max_points:
            return [ParamIssue("min_points", MIN_ABOVE_MAX)]
        return []


# ----------------------------------------------------------------------------- streak
STREAK_PRESENTATION = "streak"
"""Подача итога серией верных раундов."""

BRACKET_PRESENTATION = "bracket"
"""Подача итога сеткой выборов (очков нет)."""


class StreakParams(PluginParams):
    """Параметры ``streak`` (``contracts/modes/params/scorer.streak.v1.json``)."""

    points_per_round: Annotated[StrictInt, Field(ge=1, le=1000)]


class StreakScorer:
    """Серия: ``points_per_round`` за каждый точный ответ, остальное — 0.

    Серия заканчивается на промахе, поэтому Scorer требует правило досрочного завершения
    (``lives``): без него «серия» — просто число верных раундов из ``session.rounds``.
    """

    key: ClassVar[str] = "streak"
    params_schema: ClassVar[type[BaseModel]] = StreakParams
    presentation: ClassVar[str] = STREAK_PRESENTATION
    requires_early_end: ClassVar[bool] = True

    def score(self, outcome: RoundOutcome, params: BaseModel) -> int:
        """``points_per_round`` за ``exact``."""
        p = typed(params, StreakParams)
        return p.points_per_round if outcome.result is RoundResult.EXACT else 0

    def max_score(self, params: BaseModel, levels_count: int) -> int:
        """Максимум за раунд — ``points_per_round``."""
        return typed(params, StreakParams).points_per_round

    def cross_check(
        self, params: BaseModel, *, config: ModeConfig, levels_count: int
    ) -> list[ParamIssue]:
        """Перекрёстных правил параметров нет (совместимость с ``end`` — в валидаторе)."""
        typed(params, StreakParams)
        return []


# ----------------------------------------------------------------------------- none
class NoneParams(PluginParams):
    """Параметры ``none`` (``contracts/modes/params/scorer.none.v1.json``): нет."""


class NoneScorer:
    """Без очков: режим предпочтений (сетка), итог — выбранные победители."""

    key: ClassVar[str] = "none"
    params_schema: ClassVar[type[BaseModel]] = NoneParams
    presentation: ClassVar[str] = BRACKET_PRESENTATION
    requires_early_end: ClassVar[bool] = False

    def score(self, outcome: RoundOutcome, params: BaseModel) -> int:
        """Всегда 0."""
        typed(params, NoneParams)
        return 0

    def max_score(self, params: BaseModel, levels_count: int) -> int:
        """Всегда 0."""
        typed(params, NoneParams)
        return 0

    def cross_check(
        self, params: BaseModel, *, config: ModeConfig, levels_count: int
    ) -> list[ParamIssue]:
        """Перекрёстных правил нет."""
        typed(params, NoneParams)
        return []
