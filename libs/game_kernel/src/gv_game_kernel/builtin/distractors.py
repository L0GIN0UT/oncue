"""Встроенные источники дистракторов формата ``pick`` (ADR 0040).

* ``similar`` — неверные варианты, близкие к верной единице: общий жанр, окно по году,
  отношение популярностей; одноимённые с верной (по названию и ``title_aliases``) и сама
  верная единица исключаются.

Подбор чистый: от пула и ``rng`` (порядок кандидатов фиксируется сортировкой по ``id``),
поэтому план раундов воспроизводим по seed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, ClassVar

from pydantic import BaseModel, Field, StrictBool, StrictFloat, StrictInt

from gv_core.random import Random
from gv_game_kernel.builtin._base import PluginParams, typed
from gv_game_kernel.errors import PoolTooSmallError
from gv_game_kernel.plugins import ParamIssue
from gv_game_kernel.refs import MediaItemRef
from gv_text import CONJUNCTION_REPLACEMENTS, NormalizerConfig, TitleNormalizer

MIN_POOL_TOO_SMALL = "config.distractor_pool_too_small"
"""Код проблемы: ``min_pool`` меньше, чем нужно для ``count`` неверных вариантов + верный."""


class SimilarDistractorParams(PluginParams):
    """Параметры ``similar`` (``contracts/modes/params/distractor.similar.v1.json``)."""

    same_genre: StrictBool
    year_window: Annotated[StrictInt, Field(ge=0)] | None
    popularity_ratio: Annotated[StrictFloat, Field(ge=0, le=1)] | None
    min_pool: Annotated[StrictInt, Field(ge=1)]


# Тот же нормализатор, что у сверки ответов (``engine.answer_normalizer()``): регистр, «ё»,
# пунктуация, союзы. Здесь собран заново, чтобы builtin не зависел от engine.
_NORMALIZER = TitleNormalizer(NormalizerConfig(replacements=CONJUNCTION_REPLACEMENTS))


def _titles(item: MediaItemRef) -> frozenset[str]:
    """Названия единицы для «одноимённости»: основное и ``title_aliases``, нормализованные.

    Алиасы каталога уже нормализованы, но нормализация идемпотентна — повтор безопасен.
    """
    names = (_NORMALIZER.normalize(n) for n in (item.title, *item.title_aliases))
    return frozenset(n for n in names if n)


class SimilarDistractors:
    """Дистракторы по близости к верной единице.

    Фильтры применяются только если у верной единицы есть соответствующий признак
    (жанры, год, популярность); у кандидата без признака при активном фильтре — отказ.
    ``popularity_ratio`` — минимум отношения меньшей популярности к большей.
    """

    key: ClassVar[str] = "similar"
    params_schema: ClassVar[type[BaseModel]] = SimilarDistractorParams

    def pick(
        self,
        correct: MediaItemRef,
        pool: Sequence[MediaItemRef],
        *,
        count: int,
        params: BaseModel,
        rng: Random,
    ) -> list[MediaItemRef]:
        """``count`` различных неверных вариантов из ``pool``.

        Raises:
            PoolTooSmallError: в пуле меньше ``min_pool`` единиц или подходящих
                кандидатов меньше ``count``.
        """
        p = typed(params, SimilarDistractorParams)
        if len(pool) < p.min_pool:
            raise PoolTooSmallError(
                "в пуле не хватает единиц для дистракторов",
                details={"available": len(pool), "required": p.min_pool},
            )
        correct_titles = _titles(correct)
        unique = {i.id: i for i in pool if i.id != correct.id}
        eligible: list[MediaItemRef] = []
        seen = set(correct_titles)
        for key in sorted(unique):
            cand = unique[key]
            names = _titles(cand)
            # Одноимённые с верной и друг с другом в варианты не попадают: иначе у раунда
            # два «одинаковых» ответа.
            if names & seen or not _is_close(correct, cand, p):
                continue
            seen |= names
            eligible.append(cand)
        if len(eligible) < count:
            raise PoolTooSmallError(
                "подходящих дистракторов не хватает",
                details={"available": len(eligible), "required": count},
            )
        return rng.sample(eligible, count)

    def cross_check(self, params: BaseModel, *, count: int) -> list[ParamIssue]:
        """``min_pool`` не меньше ``count + 1`` (неверные варианты и верный)."""
        p = typed(params, SimilarDistractorParams)
        if p.min_pool < count + 1:
            return [ParamIssue("min_pool", MIN_POOL_TOO_SMALL)]
        return []


def _is_close(correct: MediaItemRef, cand: MediaItemRef, p: SimilarDistractorParams) -> bool:
    if p.same_genre and correct.genres and not set(correct.genres) & set(cand.genres):
        return False
    if (
        p.year_window is not None
        and correct.year is not None
        and (cand.year is None or abs(cand.year - correct.year) > p.year_window)
    ):
        return False
    if p.popularity_ratio is not None and correct.popularity is not None:
        if cand.popularity is None:
            return False
        high = max(correct.popularity, cand.popularity)
        if high > 0 and min(correct.popularity, cand.popularity) / high < p.popularity_ratio:
            return False
    return True
