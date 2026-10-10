"""Сверка ответов: matcher ``title_alias`` и правило частичного ответа ``artist``.

Обе стороны сравнения нормализуются одним ``ctx.normalizer`` (по умолчанию —
``gv_text`` с заменами союзов, см. ``ModeEngine``): алиасы каталога уже нормализованы
базовым нормализатором, а повторная нормализация с заменами совместима с ней
(``M(N(x)) == M(x)``, golden-тест ``libs/text``). Поэтому «Rock & Roll» в ответе совпадает
с алиасом «rock and roll».

Семантика зафиксирована golden-таблицей ``tests/golden/matcher.yaml``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, ClassVar

from pydantic import BaseModel, Field, StrictBool, StrictInt

from gv_game_kernel.builtin._base import PluginParams, typed
from gv_game_kernel.plugins import AnswerInput, MatchContext, MatchResult
from gv_game_kernel.refs import MediaItemRef
from gv_game_kernel.round import MatchVerdict
from gv_text import TitleNormalizer, damerau_levenshtein

MAX_ANSWER_LEN = 300
"""Длиннее нормализованного алиаса каталога (300) ответ совпасть не может — без расчёта."""


class TypoRule(PluginParams):
    """Допуск опечаток для ответов длиной от ``min_len`` (нормализованная строка)."""

    min_len: Annotated[StrictInt, Field(ge=1, le=200)]
    max_distance: Annotated[StrictInt, Field(ge=0, le=3)]


class TitleAliasParams(PluginParams):
    """Параметры ``title_alias`` (``contracts/modes/params/matcher.title_alias.v1.json``)."""

    typo: Annotated[tuple[TypoRule, ...], Field(max_length=10)]
    accept_same_title: StrictBool

    def tolerance(self, length: int) -> int:
        """Допуск для длины ответа: правило с наибольшим ``min_len ≤ length`` (иначе 0)."""
        fitting = [r for r in self.typo if r.min_len <= length]
        return max(fitting, key=lambda r: r.min_len).max_distance if fitting else 0


def _normalized(normalizer: TitleNormalizer, values: Iterable[str]) -> set[str]:
    return {n for n in (normalizer.normalize(v) for v in values) if n}


def item_titles(item: MediaItemRef, normalizer: TitleNormalizer) -> set[str]:
    """Все допустимые формы названия единицы: основное название и все алиасы."""
    return _normalized(normalizer, (item.title, *item.aliases))


class TitleAliasMatcher:
    """Сверка по названию и алиасам.

    * ``{media_item_id}`` → ``exact``, если выбрана загаданная единица, или
      (``accept_same_title``) основное название выбранной совпадает с названием или
      алиасом вида ``title``/``alt`` загаданной (дубли названий, ремейки);
    * ``{text}`` → нормализованный текст совпадает с названием или любым алиасом либо
      отличается не больше чем на допуск ``typo`` (Дамерау-Левенштейн);
    * иначе — первое сработавшее правило ``ctx.partial_rules`` (``partial``) или ``miss``.
    """

    key: ClassVar[str] = "title_alias"
    params_schema: ClassVar[type[BaseModel]] = TitleAliasParams

    def match(
        self, answer: AnswerInput, item: MediaItemRef, params: BaseModel, ctx: MatchContext
    ) -> MatchResult:
        """Точное попадание, частичное или промах (см. описание класса)."""
        p = typed(params, TitleAliasParams)
        if self._exact(answer, item, p, ctx.normalizer):
            return MatchResult(MatchVerdict.EXACT)
        for rule in ctx.partial_rules:
            if rule.check(answer, item, ctx):
                return MatchResult(MatchVerdict.PARTIAL, partial_kind=rule.kind)
        return MatchResult(MatchVerdict.MISS)

    @staticmethod
    def _exact(
        answer: AnswerInput, item: MediaItemRef, p: TitleAliasParams, normalizer: TitleNormalizer
    ) -> bool:
        if answer.media_item_id is not None:
            if answer.media_item_id == item.id:
                return True
            if not p.accept_same_title or answer.selected is None:
                return False
            chosen = normalizer.normalize(answer.selected.title)
            same = _normalized(normalizer, (item.title, *item.title_aliases))
            return bool(chosen) and chosen in same
        text = normalizer.normalize(answer.text or "")
        if not text or len(text) > MAX_ANSWER_LEN:
            return False
        titles = item_titles(item, normalizer)
        if text in titles:
            return True
        k = p.tolerance(len(text))
        return k > 0 and any(damerau_levenshtein(text, t, k) <= k for t in titles)


class ArtistPartial:
    """Частичный ответ «угадан исполнитель» (только музыка).

    Выбранная единица с общим исполнителем (``person`` с ролью ``artist``) или текст,
    совпавший с нормализованным именем исполнителя (или его транслитом).
    """

    kind: ClassVar[str] = "artist"
    domains: ClassVar[frozenset[str]] = frozenset({"music"})
    role: ClassVar[str] = "artist"

    def check(self, answer: AnswerInput, item: MediaItemRef, ctx: MatchContext) -> bool:
        """Является ли ответ попаданием в исполнителя ``item``."""
        artists = [p for p in item.persons if p.role == self.role]
        if not artists:
            return False
        if answer.selected is not None:
            chosen = {p.id for p in answer.selected.persons if p.role == self.role}
            return any(p.id in chosen for p in artists)
        if answer.text is None:
            return False
        text = ctx.normalizer.normalize(answer.text)
        if not text:
            return False
        names: set[str] = set()
        for person in artists:
            name = ctx.normalizer.normalize(person.name)
            names.add(name)
            translit = ctx.normalizer.translit(name)
            if translit:
                names.add(translit)
        return text in names
