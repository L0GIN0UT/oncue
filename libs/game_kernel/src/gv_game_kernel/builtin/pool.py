"""Правила пула единиц для источников (``contracts/modes/params/pool.v1.json``).

Пул конфига и фильтры пресета сужают выборку: итоговый ``PoolQuery`` — их конъюнкция.
У ``PoolQuery`` одно поле на ось, поэтому множества (``kinds``, ``genres_any``)
пересекаются, диапазоны лет сужаются, ``min_popularity`` берётся наибольший. Пустое
пересечение (пресет «comedy» при пуле «drama») не может означать «любые» — такой конфиг
отклоняет ``filters_issues`` при валидации.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, ConfigDict, Field, StrictFloat, StrictInt, model_validator

from gv_game_kernel.builtin._base import PluginParams, custom_error, unique
from gv_game_kernel.plugins import ParamIssue
from gv_game_kernel.ports import PlayableFor, PoolQuery

EMPTY_FILTER = "config.preset_filter_empty"
"""Код проблемы: фильтр пресета не пересекается с пулом (выборка была бы пустой)."""

MIN_PLAYABLE_BELOW_ROUNDS = "config.min_playable_below_rounds"
"""Код проблемы: ``pool.min_playable`` меньше числа раундов — пул опубликуют пустым."""

Genre = Annotated[str, Field(min_length=1, max_length=64)]
Kind = Literal["track", "film"]
Year = Annotated[StrictInt, Field(ge=1800, le=2100)]

_UNIQUE = {"uniqueItems": True}


class YearRange(PluginParams):
    """Диапазон лет выхода (границы включительно, любая может отсутствовать)."""

    model_config = ConfigDict(populate_by_name=True)

    from_: Year | None = Field(default=None, alias="from")
    to: Year | None = None

    @model_validator(mode="after")
    def _ordered(self) -> YearRange:
        if self.from_ is not None and self.to is not None and self.from_ > self.to:
            raise custom_error("years_range", "years.from больше years.to")
        return self


class PoolFilters(PluginParams):
    """Фильтры пресета случайного режима (подмножество полей пула)."""

    genres_any: Annotated[
        tuple[Genre, ...],
        Field(max_length=50, json_schema_extra=_UNIQUE),
        AfterValidator(unique),
    ] = ()
    kinds: (
        Annotated[
            tuple[Kind, ...],
            Field(min_length=1, json_schema_extra=_UNIQUE),
            AfterValidator(unique),
        ]
        | None
    ) = None
    min_popularity: Annotated[StrictFloat, Field(ge=0)] | None = None
    years: YearRange | None = None


class PoolParams(PoolFilters):
    """Пул источника (``pool.v1.json``): фильтры и минимум играбельных единиц."""

    min_playable: Annotated[StrictInt, Field(ge=1, le=100000)] | None = None


def _years(f: PoolFilters) -> tuple[int | None, int | None]:
    return (f.years.from_, f.years.to) if f.years else (None, None)


def _narrow_set(base: frozenset[str], extra: frozenset[str]) -> frozenset[str] | None:
    """Пересечение «любой из»-множеств; пустое множество — без фильтра; ``None`` — пусто."""
    if not base:
        return extra
    if not extra:
        return base
    return (base & extra) or None


def _max[N: (int, float)](a: N | None, b: N | None) -> N | None:
    """Наибольшая из заданных границ (``None`` — граница не задана)."""
    values = [v for v in (a, b) if v is not None]
    return max(values) if values else None


def _min(a: int | None, b: int | None) -> int | None:
    """Наименьшая из заданных границ."""
    values = [v for v in (a, b) if v is not None]
    return min(values) if values else None


def filters_issues(pool: PoolParams, extra: PoolFilters, prefix: str) -> list[ParamIssue]:
    """Оси, по которым фильтр ``extra`` не пересекается с пулом (пути с префиксом)."""
    issues: list[ParamIssue] = []
    if _narrow_set(frozenset(pool.kinds or ()), frozenset(extra.kinds or ())) is None:
        issues.append(ParamIssue(f"{prefix}.kinds", EMPTY_FILTER))
    if _narrow_set(frozenset(pool.genres_any), frozenset(extra.genres_any)) is None:
        issues.append(ParamIssue(f"{prefix}.genres_any", EMPTY_FILTER))
    (pf, pt), (ef, et) = _years(pool), _years(extra)
    lo, hi = _max(pf, ef), _min(pt, et)
    if lo is not None and hi is not None and lo > hi:
        issues.append(ParamIssue(f"{prefix}.years", EMPTY_FILTER))
    return issues


def build_query(
    pool: PoolParams,
    *,
    domain: str,
    playable_for: PlayableFor | None,
    extra: PoolFilters | None = None,
) -> PoolQuery:
    """``PoolQuery`` из пула и (необязательного) фильтра пресета.

    Raises:
        ValueError: фильтр не пересекается с пулом (валидатор конфига такое не пропускает).
    """
    f = extra or PoolFilters()
    if filters_issues(pool, f, "filters"):
        raise ValueError("фильтр пресета не пересекается с пулом")
    kinds = _narrow_set(frozenset(pool.kinds or ()), frozenset(f.kinds or ())) or frozenset()
    genres = _narrow_set(frozenset(pool.genres_any), frozenset(f.genres_any)) or frozenset()
    (pf, pt), (ef, et) = _years(pool), _years(f)
    return PoolQuery(
        domain=domain,
        kinds=kinds,
        genres_any=genres,
        year_from=_max(pf, ef),
        year_to=_min(pt, et),
        min_popularity=_max(pool.min_popularity, f.min_popularity),
        playable_for=playable_for,
    )


def min_playable(pool: PoolParams, rounds: int) -> int:
    """Минимум играбельных единиц для публикации: ``pool.min_playable`` или число раундов."""
    return max(pool.min_playable or 0, rounds)


def min_playable_issues(pool: PoolParams, rounds: int) -> list[ParamIssue]:
    """``pool.min_playable`` не может быть меньше числа раундов сессии."""
    if pool.min_playable is not None and pool.min_playable < rounds:
        return [ParamIssue("pool.min_playable", MIN_PLAYABLE_BELOW_ROUNDS)]
    return []
