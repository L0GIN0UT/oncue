"""Встроенные источники единиц раундов: ``daily``, ``random``, ``collection`` (этап 2) и
``marathon``, ``pair``, ``bracket`` (этап 3a, ADR 0023/0027).

Розыгрыш детерминирован: кандидаты сортируются по ``id``, затем
``SeededRandom(ctx.seed).sample``. Одинаковые seed и пул дают одинаковый набор, а без
секрета seed дейлика (``daily_seed``) набор не предсказать.

* ``marathon`` — все играбельные единицы выбранных игроком персон (``SubjectSource``),
  раундов ``min(пул, session.rounds)``;
* ``pair`` — цепочка «больше/меньше» по метрике (``RoundSource``, ``ComparisonSource``):
  соседние единицы различаются не меньше чем на ``min_gap`` и никогда не равны;
* ``bracket`` — плановая сетка периода (``RoundSource``, ``ProgressiveSource``): первый
  тур из посева, следующие — пары победителей прошлого тура.

``collection`` реализован на ``CollectionPort`` (ADR 0028: шаблон режима, сборник — в
``DrawContext.collection_id``) и регистрируется отдельно (``register_collection``):
``register_builtin`` его не включает.
"""

# ruff: noqa: ARG002 — методы реализуют протоколы плагинов, не всем нужны все аргументы

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Annotated, Any, ClassVar, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    field_validator,
)

from gv_core.random import SeededRandom
from gv_game_kernel.builtin._base import PluginParams, custom_error, typed, unique
from gv_game_kernel.builtin.clues import CLASSIC_FORMAT, PICK_FORMAT
from gv_game_kernel.builtin.matching import HIGHER, LOWER
from gv_game_kernel.builtin.pool import (
    PoolFilters,
    PoolParams,
    build_query,
    filters_issues,
    min_playable,
    min_playable_issues,
)
from gv_game_kernel.errors import (
    CollectionRequiredError,
    PoolTooSmallError,
    PresetNotFoundError,
    RoundsNotAllowedError,
    SubjectRequiredError,
    TooManySubjectsError,
)
from gv_game_kernel.plugins import (
    Comparison,
    DrawContext,
    OptionSpec,
    ParamIssue,
    Preset,
    RoundDraw,
    RoundOption,
    RoundPick,
)
from gv_game_kernel.ports import PlayableFor, PoolQuery, SubjectQuery
from gv_game_kernel.refs import MediaItemRef
from gv_game_kernel.spec import I18nKey, ModeConfig

ROUNDS_MISMATCH = "config.rounds_mismatch"
"""Код проблемы: размер набора дня не равен числу раундов сессии."""

DUPLICATE_PRESET = "config.duplicate_preset"
"""Код проблемы: два пресета с одним ``id``."""

PresetId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]


def daily_seed(secret: bytes, mode_key: str, day: date) -> bytes:
    """Seed набора дня: ``hmac_sha256(secret, "<mode_key>|<YYYY-MM-DD>")``.

    Детерминирован (повторная генерация набора даёт тот же результат) и непредсказуем без
    секрета (D6). Секрет хранит game в настройках окружения.

    Raises:
        ValueError: пустой секрет.
    """
    if not secret:
        raise ValueError("секрет набора дня не может быть пустым")
    message = f"{mode_key}|{day.isoformat()}".encode()
    return hmac.new(secret, message, hashlib.sha256).digest()


def _pool_too_small(available: int, required: int) -> PoolTooSmallError:
    return PoolTooSmallError(
        "в пуле не хватает играбельных единиц",
        details={"available": available, "required": required},
    )


def _sample(items: Sequence[MediaItemRef], n: int, rng: SeededRandom) -> list[MediaItemRef]:
    """``n`` различных единиц из ``items``; порядок кандидатов фиксируется сортировкой."""
    unique = {item.id: item for item in items}
    ordered = [unique[key] for key in sorted(unique)]
    if len(ordered) < n:
        raise _pool_too_small(len(ordered), n)
    return rng.sample(ordered, n)


def _require_n(n: int) -> None:
    if n < 1:
        raise ValueError("нужно разыграть хотя бы одну единицу")


# ----------------------------------------------------------------------------- daily
class DailyParams(PluginParams):
    """Параметры ``daily`` (``contracts/modes/params/source.daily.v1.json``)."""

    size: Annotated[StrictInt, Field(ge=1, le=50)]
    timezone: Annotated[str, Field(min_length=1, max_length=64)]
    schedule_ahead_days: Annotated[StrictInt, Field(ge=0, le=30)]
    exclude_recent_days: Annotated[StrictInt, Field(ge=0, le=365)]
    pool: PoolParams

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        return known_zone(value)


def known_zone(value: str) -> str:
    """Валидатор IANA-зоны: неизвестная — ошибка ``schema.timezone_unknown``."""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise custom_error("timezone_unknown", "неизвестная IANA-зона {zone}", zone=value) from exc
    return value


def _local_date(at: datetime, timezone: str) -> date:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("момент должен быть с часовым поясом")
    return at.astimezone(ZoneInfo(timezone)).date()


CLASSIC_ONLY = frozenset({CLASSIC_FORMAT})
PICK_ONLY = frozenset({PICK_FORMAT})


class DailySource:
    """Набор дня по правилам пула (D6).

    Кандидаты — играбельные под параметры загадки единицы пула (``playable_for``). Единицы
    из наборов последних ``exclude_recent_days`` дней (``ctx.exclude_ids``, их собирает
    game) исключаются; если свежих не хватает, недостающие добираются из недавних — пустой
    день хуже повтора.
    """

    key: ClassVar[str] = "daily"
    params_schema: ClassVar[type[BaseModel]] = DailyParams
    answer_formats: ClassVar[frozenset[str]] = CLASSIC_ONLY

    async def draw(self, ctx: DrawContext, params: BaseModel, n: int) -> list[MediaItemRef]:
        """``n`` единиц набора дня (детерминированно по ``ctx.seed``).

        Raises:
            PoolTooSmallError: даже с недавними единицами пул меньше ``n``.
        """
        _require_n(n)
        p = typed(params, DailyParams)
        base = self._query(p, ctx)
        rng = SeededRandom(ctx.seed)
        fresh = await ctx.candidates.candidates(
            _with_exclude(base, ctx.exclude_ids), ctx.candidate_limit
        )
        fresh = [item for item in fresh if item.id not in ctx.exclude_ids]
        if len({item.id for item in fresh}) >= n or not ctx.exclude_ids:
            return _sample(fresh, n, rng)
        # Свежих не хватает: берём все свежие и добираем из недавних.
        everything = await ctx.candidates.candidates(base, ctx.candidate_limit)
        fresh_ids = {item.id for item in fresh}
        recent = [item for item in everything if item.id not in fresh_ids]
        picked = _sample(fresh, len(fresh_ids), rng) if fresh_ids else []
        missing = n - len(picked)
        if len({item.id for item in recent}) < missing:
            raise _pool_too_small(len(fresh_ids) + len({i.id for i in recent}), n)
        return picked + _sample(recent, missing, rng)

    @staticmethod
    def _query(p: DailyParams, ctx: DrawContext) -> PoolQuery:
        return build_query(
            p.pool,
            domain=ctx.domain,
            playable_for=PlayableFor(clue_type=ctx.clue_type, params_hash=ctx.params_hash),
        )

    def presets(self, params: BaseModel) -> list[Preset]:
        """У дейлика пресетов нет."""
        typed(params, DailyParams)
        return []

    def cross_check(
        self, params: BaseModel, *, config: ModeConfig, levels_count: int
    ) -> list[ParamIssue]:
        """``size == session.rounds``; ``pool.min_playable ≥ rounds``."""
        p = typed(params, DailyParams)
        issues = min_playable_issues(p.pool, config.session.rounds)
        if p.size != config.session.rounds:
            issues.insert(0, ParamIssue("size", ROUNDS_MISMATCH))
        return issues

    def pool_query(
        self,
        params: BaseModel,
        *,
        domain: str,
        playable_for: PlayableFor | None,
        preset_id: str | None = None,
    ) -> PoolQuery:
        """Запрос пула дейлика (пресетов нет: ``preset_id`` должен быть ``None``).

        Raises:
            PresetNotFoundError: передан ``preset_id``.
        """
        p = typed(params, DailyParams)
        if preset_id is not None:
            raise PresetNotFoundError("у режима нет пресетов", details={"preset_id": preset_id})
        return build_query(p.pool, domain=domain, playable_for=playable_for)

    def min_playable(self, params: BaseModel, *, rounds: int) -> int:
        """``pool.min_playable`` (не меньше числа раундов)."""
        return min_playable(typed(params, DailyParams).pool, rounds)

    @staticmethod
    def day_of(params: BaseModel, at: datetime) -> date:
        """День дейлика для момента ``at``: смена дня — 00:00 в ``params.timezone``.

        Raises:
            ValueError: ``at`` без часового пояса.
        """
        return _local_date(at, typed(params, DailyParams).timezone)

    def period_start(self, params: BaseModel, at: datetime) -> date:
        """Начало периода набора (``PeriodSource``): у дейлика — день ``day_of``."""
        return self.day_of(params, at)

    def theme_query(
        self, params: BaseModel, *, domain: str, playable_for: PlayableFor | None
    ) -> None:
        """У дейлика нет темы периода."""
        typed(params, DailyParams)


def _with_exclude(query: PoolQuery, exclude: frozenset[UUID]) -> PoolQuery:
    """Запрос с исключёнными единицами (остальные поля, включая персоны и метрику, — как были)."""
    if not exclude:
        return query
    return replace(query, exclude_ids=exclude)


# ----------------------------------------------------------------------------- random
class PresetParams(PluginParams):
    """Пресет случайного режима: ``id``, ключ названия и фильтры поверх пула."""

    id: PresetId
    title_i18n: I18nKey
    filters: PoolFilters


class RandomParams(PluginParams):
    """Параметры ``random`` (``contracts/modes/params/source.random.v1.json``)."""

    pool: PoolParams
    presets: Annotated[tuple[PresetParams, ...], Field(max_length=50)] = ()


class RandomSource:
    """Случайные единицы пула, сужённого выбранным пресетом; seed — seed сессии."""

    key: ClassVar[str] = "random"
    params_schema: ClassVar[type[BaseModel]] = RandomParams
    answer_formats: ClassVar[frozenset[str]] = CLASSIC_ONLY

    async def draw(self, ctx: DrawContext, params: BaseModel, n: int) -> list[MediaItemRef]:
        """``n`` единиц пула ∧ фильтров пресета ``ctx.preset_id``.

        Raises:
            PresetNotFoundError: неизвестный пресет.
            PoolTooSmallError: в выборке меньше ``n`` единиц.
        """
        _require_n(n)
        query = self.pool_query(
            params,
            domain=ctx.domain,
            playable_for=PlayableFor(clue_type=ctx.clue_type, params_hash=ctx.params_hash),
            preset_id=ctx.preset_id,
        )
        query = _with_exclude(query, ctx.exclude_ids)
        items = await ctx.candidates.candidates(query, ctx.candidate_limit)
        items = [item for item in items if item.id not in ctx.exclude_ids]
        return _sample(items, n, SeededRandom(ctx.seed))

    def presets(self, params: BaseModel) -> list[Preset]:
        """Пресеты в порядке конфига."""
        p = typed(params, RandomParams)
        return [Preset(id=x.id, title_key=x.title_i18n) for x in p.presets]

    def cross_check(
        self, params: BaseModel, *, config: ModeConfig, levels_count: int
    ) -> list[ParamIssue]:
        """Фильтры каждого пресета пересекаются с пулом, ``id`` пресетов уникальны,
        ``pool.min_playable ≥ rounds``."""
        p = typed(params, RandomParams)
        issues = min_playable_issues(p.pool, config.session.rounds)
        seen: set[str] = set()
        for i, preset in enumerate(p.presets):
            if preset.id in seen:
                issues.append(ParamIssue(f"presets[{i}].id", DUPLICATE_PRESET))
            seen.add(preset.id)
            issues.extend(filters_issues(p.pool, preset.filters, f"presets[{i}].filters"))
        return issues

    def pool_query(
        self,
        params: BaseModel,
        *,
        domain: str,
        playable_for: PlayableFor | None,
        preset_id: str | None = None,
    ) -> PoolQuery:
        """Запрос пула с фильтрами пресета (``None`` — весь пул).

        Raises:
            PresetNotFoundError: неизвестный ``preset_id``.
        """
        p = typed(params, RandomParams)
        extra = None
        if preset_id is not None:
            found = next((x for x in p.presets if x.id == preset_id), None)
            if found is None:
                raise PresetNotFoundError(
                    "у режима нет такого пресета", details={"preset_id": preset_id}
                )
            extra = found.filters
        return build_query(p.pool, domain=domain, playable_for=playable_for, extra=extra)

    def min_playable(self, params: BaseModel, *, rounds: int) -> int:
        """``pool.min_playable`` (не меньше числа раундов) — для пула без пресета."""
        return min_playable(typed(params, RandomParams).pool, rounds)


# ----------------------------------------------------------------------------- collection
ROUND_OPTION_ABOVE_ROUNDS = "config.round_option_above_rounds"
"""Код проблемы: вариант числа раундов сборника больше ``session.rounds``."""

RoundOptions = Annotated[
    tuple[RoundOption, ...],
    Field(min_length=1, max_length=4, json_schema_extra={"uniqueItems": True}),
    AfterValidator(unique),
]


class CollectionParams(PluginParams):
    """Параметры ``collection`` (``contracts/modes/params/source.collection.v1.json``).

    Конфиг — шаблон режима для всех сборников домена (ADR 0028): сам сборник приходит при
    старте сессии и лежит в ``DrawContext.collection_id``.

    Attributes:
        round_options: Из чего выбирает игрок: подмножество ``{5, 10, 25, "all"}``.
        min_items: Минимум играбельных единиц сборника, чтобы режим был доступен.
        shuffle: Перемешать единицы (иначе — первые ``n`` в порядке сборника).
        max_collections: Сколько сборников можно выбрать в лобби матча (этап 4, D12);
            соло — всегда один.
    """

    round_options: RoundOptions
    min_items: Annotated[StrictInt, Field(ge=1, le=200)]
    shuffle: StrictBool = True
    max_collections: Annotated[StrictInt, Field(ge=1, le=10)] = 1


class CollectionSource:
    """Единицы сборника через ``CollectionPort`` (ADR 0028; в game — ``register_collection``).

    Единицы — играбельные под загадку режима (``PlayableFor`` со ``scope`` сборника: свой
    набор сборника, иначе каталожный), без повторов, в порядке автора. При ``shuffle``
    розыгрыш детерминирован по ``ctx.seed`` (тот же seed и состав — тот же набор), без него
    берутся первые ``n``. Единицы из ``ctx.exclude_ids`` берутся, только если остальных не
    хватает на ``n`` (как у дейлика: пустая сессия хуже повтора).
    """

    key: ClassVar[str] = "collection"
    params_schema: ClassVar[type[BaseModel]] = CollectionParams
    answer_formats: ClassVar[frozenset[str]] = CLASSIC_ONLY

    def round_options(self, params: BaseModel) -> list[RoundOption]:
        """Варианты числа раундов в порядке конфига (первый — по умолчанию)."""
        return list(typed(params, CollectionParams).round_options)

    def rounds_for(self, params: BaseModel, requested: RoundOption | None, pool: int) -> int:
        """Сколько раундов разыграть по выбору игрока и размеру играбельного пула сборника.

        ``"all"`` — весь пул; число — ровно столько, если оно есть в ``round_options`` и
        не больше пула; ``None`` — первый вариант ``round_options``.

        Raises:
            PoolTooSmallError: в пуле меньше ``min_items`` или меньше запрошенного числа.
            RoundsNotAllowedError: запрошенного варианта нет в ``round_options``.
        """
        p = typed(params, CollectionParams)
        choice = p.round_options[0] if requested is None else requested
        if choice not in p.round_options:
            raise RoundsNotAllowedError(
                "такого числа раундов у режима нет",
                details={"requested": choice, "options": list(p.round_options)},
            )
        if pool < p.min_items:
            raise _pool_too_small(pool, p.min_items)
        if choice == "all":
            return pool
        if pool < choice:
            raise _pool_too_small(pool, choice)
        return choice

    @staticmethod
    async def _items(ctx: DrawContext) -> list[MediaItemRef]:
        """Играбельные единицы сборника без повторов в порядке сборника."""
        if ctx.collections is None:
            raise ValueError("источнику collection нужен CollectionPort")
        if ctx.collection_id is None:
            raise CollectionRequiredError("источнику collection нужен сборник")
        items = await ctx.collections.items(
            ctx.collection_id,
            ctx.candidate_limit,
            playable_for=PlayableFor(
                clue_type=ctx.clue_type, params_hash=ctx.params_hash, scope=ctx.collection_id
            ),
        )
        seen: set[UUID] = set()
        ordered: list[MediaItemRef] = []
        for item in items:
            if item.id not in seen:
                seen.add(item.id)
                ordered.append(item)
        return ordered

    async def pool_size(self, ctx: DrawContext, params: BaseModel) -> int:
        """Число различных играбельных единиц сборника (вход ``rounds_for``).

        Raises:
            ValueError: в контексте нет ``CollectionPort``.
            CollectionRequiredError: в контексте нет ``collection_id``.
        """
        typed(params, CollectionParams)
        return len(await self._items(ctx))

    async def draw(self, ctx: DrawContext, params: BaseModel, n: int) -> list[MediaItemRef]:
        """``n`` единиц сборника ``ctx.collection_id`` (по ``ctx.seed`` или по порядку).

        Raises:
            ValueError: в контексте нет ``CollectionPort`` или ``n < 1``.
            CollectionRequiredError: в контексте нет ``collection_id``.
            PoolTooSmallError: в сборнике меньше ``min_items`` или меньше ``n`` единиц.
        """
        _require_n(n)
        p = typed(params, CollectionParams)
        ordered = await self._items(ctx)
        if len(ordered) < p.min_items:
            raise _pool_too_small(len(ordered), p.min_items)
        if len(ordered) < n:
            raise _pool_too_small(len(ordered), n)
        fresh = [item for item in ordered if item.id not in ctx.exclude_ids]
        recent = [item for item in ordered if item.id in ctx.exclude_ids]
        if not p.shuffle:
            chosen = {item.id for item in [*fresh, *recent][:n]}
            return [item for item in ordered if item.id in chosen]
        rng = SeededRandom(ctx.seed)
        if len(fresh) >= n:
            return rng.sample(fresh, n)
        return rng.sample(fresh, len(fresh)) + rng.sample(recent, n - len(fresh))

    def presets(self, params: BaseModel) -> list[Preset]:
        """У сборника пресетов нет."""
        typed(params, CollectionParams)
        return []

    def cross_check(
        self, params: BaseModel, *, config: ModeConfig, levels_count: int
    ) -> list[ParamIssue]:
        """Каждый числовой вариант ``round_options`` не больше ``session.rounds``
        (``session.rounds`` — потолок сессии, им же ограничивается ``"all"``)."""
        p = typed(params, CollectionParams)
        return [
            ParamIssue(f"round_options[{i}]", ROUND_OPTION_ABOVE_ROUNDS)
            for i, option in enumerate(p.round_options)
            if option != "all" and option > config.session.rounds
        ]

    def pool_query(
        self,
        params: BaseModel,
        *,
        domain: str,
        playable_for: PlayableFor | None,
        preset_id: str | None = None,
    ) -> None:
        """Сборник не берёт единицы из пула."""
        typed(params, CollectionParams)

    def min_playable(self, params: BaseModel, *, rounds: int) -> int:
        """Минимум играбельных единиц сборника для режима — ``min_items``."""
        return typed(params, CollectionParams).min_items


# ----------------------------------------------------------------------------- общее 3a
_UNIQUE = {"uniqueItems": True}

Role = Literal["artist", "director", "actor"]
Roles = Annotated[
    tuple[Role, ...],
    Field(min_length=1, max_length=3, json_schema_extra=_UNIQUE),
    AfterValidator(unique),
]

METRIC_UNSUPPORTED = "config.metric_unsupported"
"""Код проблемы: у единиц домена режима нет такой метрики (музыка — только ``popularity``)."""

SUBJECT_MIN_ITEMS_BELOW_SIZE = "config.subject_min_items_below_size"
"""Код проблемы: у темы сетки может оказаться меньше единиц, чем мест в сетке."""


def _ordered_unique(items: Sequence[MediaItemRef]) -> list[MediaItemRef]:
    """Единицы без повторов в порядке ``id`` (детерминизм розыгрыша)."""
    by_id = {item.id: item for item in items}
    return [by_id[key] for key in sorted(by_id)]


def _playable(ctx: DrawContext) -> PlayableFor:
    return PlayableFor(clue_type=ctx.clue_type, params_hash=ctx.params_hash)


def _no_presets(preset_id: str | None) -> None:
    if preset_id is not None:
        raise PresetNotFoundError("у режима нет пресетов", details={"preset_id": preset_id})


# ----------------------------------------------------------------------------- marathon
class MarathonParams(PluginParams):
    """Параметры ``marathon`` (``contracts/modes/params/source.marathon.v1.json``)."""

    roles: Roles
    max_subjects: Annotated[StrictInt, Field(ge=1, le=10)]
    min_items: Annotated[StrictInt, Field(ge=1, le=1000)]
    order: Literal["shuffle", "year"]
    pool: PoolParams


def _by_year(item: MediaItemRef) -> tuple[bool, int, UUID]:
    """Ключ порядка ``year``: по году, единицы без года — в конце, ничьи — по ``id``."""
    return (item.year is None, item.year or 0, item.id)


# Порядок раундов марафона: ключ сортировки разыгранных единиц или None — порядок розыгрыша.
# Any: ключ сортировки — кортеж сравнимых значений.
_MARATHON_ORDER: Mapping[str, Callable[[MediaItemRef], Any] | None] = {
    "shuffle": None,
    "year": _by_year,
}


def _subjects(subject_ids: frozenset[UUID], limit: int) -> frozenset[UUID]:
    if not subject_ids:
        raise SubjectRequiredError("выберите хотя бы одну персону")
    if len(subject_ids) > limit:
        raise TooManySubjectsError(
            "выбрано слишком много персон", details={"given": len(subject_ids), "max": limit}
        )
    return subject_ids


class MarathonSource:
    """Марафон по персонам (D14): все играбельные единицы пула, где есть хотя бы одна из
    выбранных персон (``ctx.subject_ids``).

    Раундов — ``min(пул, n)``: марафон заканчивается, когда пул исчерпан или кончились
    «жизни» (``session.end``). Порядок — по seed (``shuffle``) или по году (``year``).
    """

    key: ClassVar[str] = "marathon"
    params_schema: ClassVar[type[BaseModel]] = MarathonParams
    answer_formats: ClassVar[frozenset[str]] = CLASSIC_ONLY

    async def draw(self, ctx: DrawContext, params: BaseModel, n: int) -> list[MediaItemRef]:
        """До ``n`` единиц выбранных персон.

        Raises:
            SubjectRequiredError: персоны не выбраны.
            TooManySubjectsError: выбрано больше ``max_subjects``.
            PoolTooSmallError: у персон нет ни одной играбельной единицы.
        """
        _require_n(n)
        p = typed(params, MarathonParams)
        subjects = _subjects(ctx.subject_ids, p.max_subjects)
        query = replace(
            build_query(p.pool, domain=ctx.domain, playable_for=_playable(ctx)),
            person_ids_any=subjects,
            exclude_ids=ctx.exclude_ids,
        )
        found = await ctx.candidates.candidates(query, ctx.candidate_limit)
        items = _ordered_unique(
            [
                item
                for item in found
                if item.id not in ctx.exclude_ids and any(x.id in subjects for x in item.persons)
            ]
        )
        if not items:
            raise _pool_too_small(0, 1)
        picked = SeededRandom(ctx.seed).sample(items, min(n, len(items)))
        order = _MARATHON_ORDER[p.order]
        return sorted(picked, key=order) if order is not None else picked

    def presets(self, params: BaseModel) -> list[Preset]:
        """Пресетов нет: игрок выбирает персон."""
        typed(params, MarathonParams)
        return []

    def cross_check(
        self, params: BaseModel, *, config: ModeConfig, levels_count: int
    ) -> list[ParamIssue]:
        """Перекрёстных правил нет: ``session.rounds`` — максимум раундов, а не размер пула."""
        typed(params, MarathonParams)
        return []

    def pool_query(
        self,
        params: BaseModel,
        *,
        domain: str,
        playable_for: PlayableFor | None,
        preset_id: str | None = None,
    ) -> PoolQuery:
        """Весь пул режима (без фильтра персон).

        Raises:
            PresetNotFoundError: передан ``preset_id``.
        """
        p = typed(params, MarathonParams)
        _no_presets(preset_id)
        return build_query(p.pool, domain=domain, playable_for=playable_for)

    def min_playable(self, params: BaseModel, *, rounds: int) -> int:
        """``pool.min_playable``, но не меньше ``min_items`` (хотя бы одна персона)."""
        p = typed(params, MarathonParams)
        return max(p.pool.min_playable or 0, p.min_items)

    def subject_query(
        self, params: BaseModel, *, domain: str, playable_for: PlayableFor | None = None
    ) -> SubjectQuery:
        """Персоны с ролями ``roles`` и не меньше ``min_items`` играбельных единиц."""
        p = typed(params, MarathonParams)
        return SubjectQuery(
            domain=domain,
            roles=frozenset(p.roles),
            min_items=p.min_items,
            playable_for=playable_for,
        )

    def max_subjects(self, params: BaseModel) -> int:
        """Сколько персон можно выбрать."""
        return typed(params, MarathonParams).max_subjects


# ----------------------------------------------------------------------------- pair
Metric = Literal["vote_average", "revenue_usd", "runtime_min", "year", "popularity"]

HIGHER_LABEL = "game.options.higher"
LOWER_LABEL = "game.options.lower"
PAIR_OPTIONS = (
    OptionSpec(id=HIGHER, label_key=HIGHER_LABEL),
    OptionSpec(id=LOWER, label_key=LOWER_LABEL),
)
"""Варианты раунда пары: про единицу за вариантом клиент ничего не узнаёт."""

PAIR_MIN_ITEMS = 2
"""Единиц на одну пару: опорная и загаданная."""


class PairParams(PluginParams):
    """Параметры ``pair`` (``contracts/modes/params/source.pair.v1.json``)."""

    metric: Metric
    min_gap: Annotated[StrictFloat, Field(ge=0)]
    ties: Literal["both_correct"]
    pool: PoolParams


def _far(a: float | None, b: float | None, gap: float) -> bool:
    """Метрики соседей различаются: не равны и не ближе ``gap``."""
    if a is None or b is None:
        return False
    delta = abs(a - b)
    return delta > 0 and delta >= gap


def _chain(
    items: Sequence[MediaItemRef], metric: str, gap: float, limit: int
) -> list[MediaItemRef]:
    """Жадная цепочка: следующая единица — первая из оставшихся (в порядке розыгрыша),
    чья метрика достаточно отличается от последней. Нет такой — цепочка обрывается."""
    if not items:
        return []
    chain, rest = [items[0]], list(items[1:])
    while len(chain) < limit:
        last = chain[-1].metric(metric)
        index = next((i for i, x in enumerate(rest) if _far(x.metric(metric), last, gap)), None)
        if index is None:
            break
        chain.append(rest.pop(index))
    return chain


class PairSource:
    """«Больше/меньше» (D9): цепочка единиц пула с метрикой ``metric``.

    Раунд ``i`` показывает опорную единицу ``chain[i]`` с её метрикой (``shown``),
    загаданная — ``chain[i+1]`` (метрика скрыта до ревила), варианты — ``higher``/``lower``.
    Загаданная единица раунда становится опорной в следующем. Соседи цепочки не равны по
    метрике и различаются не меньше чем на ``min_gap``; единицы без метрики не играбельны.

    Attributes:
        metrics_by_domain: Метрики каталога, которые есть у единиц домена.
    """

    key: ClassVar[str] = "pair"
    params_schema: ClassVar[type[BaseModel]] = PairParams
    answer_formats: ClassVar[frozenset[str]] = PICK_ONLY
    metrics_by_domain: ClassVar[Mapping[str, frozenset[str]]] = {
        "film": frozenset({"vote_average", "revenue_usd", "runtime_min", "year", "popularity"}),
        "music": frozenset({"popularity", "year"}),
    }

    async def draw(self, ctx: DrawContext, params: BaseModel, n: int) -> list[MediaItemRef]:
        """Цепочка до ``n + 1`` единиц (на ``n`` раундов).

        Raises:
            PoolTooSmallError: не набирается даже одна пара.
        """
        _require_n(n)
        p = typed(params, PairParams)
        query = _with_exclude(
            self._query(p, domain=ctx.domain, playable_for=_playable(ctx)), ctx.exclude_ids
        )
        found = await ctx.candidates.candidates(query, ctx.candidate_limit)
        items = _ordered_unique(
            [
                item
                for item in found
                if item.id not in ctx.exclude_ids and item.metric(p.metric) is not None
            ]
        )
        shuffled = SeededRandom(ctx.seed).sample(items, len(items))
        chain = _chain(shuffled, p.metric, p.min_gap, n + 1)
        if len(chain) < PAIR_MIN_ITEMS:
            raise _pool_too_small(len(chain), PAIR_MIN_ITEMS)
        return chain

    async def draw_rounds(self, ctx: DrawContext, params: BaseModel, n: int) -> list[RoundDraw]:
        """До ``n`` раундов цепочки (см. ``draw``)."""
        return self.compose_rounds(params, await self.draw(ctx, params, n))

    def compose_rounds(self, params: BaseModel, items: Sequence[MediaItemRef]) -> list[RoundDraw]:
        """Раунды из цепочки: опорная ``chain[i]``, загаданная ``chain[i+1]``."""
        typed(params, PairParams)
        return [
            RoundDraw(target=items[i + 1], shown=(items[i],), options=PAIR_OPTIONS)
            for i in range(len(items) - 1)
        ]

    def comparison(self, params: BaseModel) -> Comparison:
        """Метрика и правило ничьей (``ComparisonSource``)."""
        p = typed(params, PairParams)
        return Comparison(metric=p.metric, ties=p.ties)

    def presets(self, params: BaseModel) -> list[Preset]:
        """Пресетов нет."""
        typed(params, PairParams)
        return []

    def cross_check(
        self, params: BaseModel, *, config: ModeConfig, levels_count: int
    ) -> list[ParamIssue]:
        """Метрика есть у единиц домена режима."""
        p = typed(params, PairParams)
        if p.metric not in self.metrics_by_domain.get(config.domain, frozenset()):
            return [ParamIssue("metric", METRIC_UNSUPPORTED)]
        return []

    @staticmethod
    def _query(p: PairParams, *, domain: str, playable_for: PlayableFor | None) -> PoolQuery:
        return replace(
            build_query(p.pool, domain=domain, playable_for=playable_for), has_metric=p.metric
        )

    def pool_query(
        self,
        params: BaseModel,
        *,
        domain: str,
        playable_for: PlayableFor | None,
        preset_id: str | None = None,
    ) -> PoolQuery:
        """Пул только из единиц с метрикой.

        Raises:
            PresetNotFoundError: передан ``preset_id``.
        """
        p = typed(params, PairParams)
        _no_presets(preset_id)
        return self._query(p, domain=domain, playable_for=playable_for)

    def min_playable(self, params: BaseModel, *, rounds: int) -> int:
        """``pool.min_playable``, но не меньше одной пары."""
        p = typed(params, PairParams)
        return max(p.pool.min_playable or 0, PAIR_MIN_ITEMS)


# ----------------------------------------------------------------------------- bracket
class BracketSubject(PluginParams):
    """Тема сетки: роли персоны и минимум её играбельных единиц."""

    roles: Roles
    min_items: Annotated[StrictInt, Field(ge=2, le=1000)]


class BracketParams(PluginParams):
    """Параметры ``bracket`` (``contracts/modes/params/source.bracket.v1.json``)."""

    size: Literal[8, 16, 32]
    period: Literal["week"]
    timezone: Annotated[str, Field(min_length=1, max_length=64)]
    schedule_ahead_periods: Annotated[StrictInt, Field(ge=0, le=8)]
    subject: BracketSubject
    pool: PoolParams

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        return known_zone(value)


BRACKET_OPTION_IDS = ("a", "b")
"""Варианты раунда сетки: первая и вторая единица пары."""


def _match(a: MediaItemRef, b: MediaItemRef) -> RoundDraw:
    """Раунд сетки: обе единицы видны, выбор — одна из них."""
    first, second = BRACKET_OPTION_IDS
    return RoundDraw(
        target=a,
        shown=(a, b),
        options=(OptionSpec(id=first, item=a), OptionSpec(id=second, item=b)),
    )


def _winner(pick: RoundPick) -> MediaItemRef:
    """Победитель раунда сетки: выбранный вариант; без выбора (``void``) — первый."""
    options = pick.draw.options
    fallback = options[0] if options else None
    chosen = next((o for o in options if o.id == pick.option_id), fallback)
    if chosen is None or chosen.item is None:
        raise ValueError("раунд сетки без вариантов-единиц")
    return chosen.item


def _stages(size: int) -> list[tuple[int, int, int]]:
    """Туры сетки: ``(участников, индекс первого раунда, раундов в туре)``."""
    stages: list[tuple[int, int, int]] = []
    start, players = 0, size
    while players >= PAIR_MIN_ITEMS:
        stages.append((players, start, players // 2))
        start += players // 2
        players //= 2
    return stages


class BracketSource:
    """Сетка «топ-N» на период (D10): ``size`` единиц темы, посев по seed периода.

    Первый тур — ``size / 2`` пар в порядке посева, каждый следующий — пары победителей
    прошлого тура (``followups``), всего ``size - 1`` выборов. Набор периода game хранит
    как набор дня (seed — ``daily_seed(secret, mode_key, period_start)``), тему —
    передаёт в ``ctx.subject_ids`` (пусто — весь пул).
    """

    key: ClassVar[str] = "bracket"
    params_schema: ClassVar[type[BaseModel]] = BracketParams
    answer_formats: ClassVar[frozenset[str]] = PICK_ONLY

    async def draw(self, ctx: DrawContext, params: BaseModel, n: int) -> list[MediaItemRef]:
        """``size`` единиц набора периода в порядке посева.

        Raises:
            ValueError: ``n`` не равно ``size``.
            PoolTooSmallError: у темы меньше ``size`` играбельных единиц.
        """
        p = typed(params, BracketParams)
        if n != p.size:
            raise ValueError(f"сетке нужно ровно {p.size} единиц, запрошено {n}")
        query = replace(
            build_query(p.pool, domain=ctx.domain, playable_for=_playable(ctx)),
            person_ids_any=ctx.subject_ids,
            exclude_ids=ctx.exclude_ids,
        )
        found = await ctx.candidates.candidates(query, ctx.candidate_limit)
        items = [item for item in found if item.id not in ctx.exclude_ids]
        return _sample(items, p.size, SeededRandom(ctx.seed))

    async def draw_rounds(self, ctx: DrawContext, params: BaseModel, n: int) -> list[RoundDraw]:
        """Первый тур (``size / 2`` раундов); ``n`` не используется — туры строит ``followups``."""
        p = typed(params, BracketParams)
        return self.compose_rounds(p, await self.draw(ctx, p, p.size))

    def compose_rounds(self, params: BaseModel, items: Sequence[MediaItemRef]) -> list[RoundDraw]:
        """Первый тур из набора периода: пары ``(0, 1), (2, 3) …``.

        Raises:
            ValueError: в наборе не ``size`` единиц.
        """
        p = typed(params, BracketParams)
        if len(items) != p.size:
            raise ValueError(f"в наборе сетки должно быть {p.size} единиц, а не {len(items)}")
        return [_match(items[i], items[i + 1]) for i in range(0, p.size, 2)]

    def planned_rounds(self, params: BaseModel) -> int:
        """``size - 1`` выборов."""
        return typed(params, BracketParams).size - 1

    def items_needed(self, params: BaseModel) -> int:
        """``size`` единиц."""
        return typed(params, BracketParams).size

    def stage(self, params: BaseModel, index: int) -> int:
        """Размер тура раунда ``index``: 32, 16 … 2.

        Raises:
            ValueError: раунда с таким индексом в сетке нет.
        """
        for players, start, count in _stages(typed(params, BracketParams).size):
            if start <= index < start + count:
                return players
        raise ValueError(f"в сетке нет раунда {index}")

    def followups(self, params: BaseModel, history: Sequence[RoundPick]) -> list[RoundDraw]:
        """Следующий тур, когда сыгран весь текущий; иначе (и после финала) — пусто."""
        done = len(history)
        for _players, start, count in _stages(typed(params, BracketParams).size)[:-1]:
            if done == start + count:
                winners = [_winner(pick) for pick in history[start : start + count]]
                return [_match(winners[i], winners[i + 1]) for i in range(0, count, 2)]
        return []

    def presets(self, params: BaseModel) -> list[Preset]:
        """Пресетов нет."""
        typed(params, BracketParams)
        return []

    def cross_check(
        self, params: BaseModel, *, config: ModeConfig, levels_count: int
    ) -> list[ParamIssue]:
        """``session.rounds == size - 1``; ``subject.min_items ≥ size``;
        ``pool.min_playable ≥ size``."""
        p = typed(params, BracketParams)
        issues = min_playable_issues(p.pool, p.size)
        if config.session.rounds != p.size - 1:
            issues.insert(0, ParamIssue("size", ROUNDS_MISMATCH))
        if p.subject.min_items < p.size:
            issues.append(ParamIssue("subject.min_items", SUBJECT_MIN_ITEMS_BELOW_SIZE))
        return issues

    def pool_query(
        self,
        params: BaseModel,
        *,
        domain: str,
        playable_for: PlayableFor | None,
        preset_id: str | None = None,
    ) -> PoolQuery:
        """Пул сетки (без темы).

        Raises:
            PresetNotFoundError: передан ``preset_id``.
        """
        p = typed(params, BracketParams)
        _no_presets(preset_id)
        return build_query(p.pool, domain=domain, playable_for=playable_for)

    def min_playable(self, params: BaseModel, *, rounds: int) -> int:
        """``pool.min_playable``, но не меньше ``size``."""
        p = typed(params, BracketParams)
        return max(p.pool.min_playable or 0, p.size)

    def period_start(self, params: BaseModel, at: datetime) -> date:
        """Начало периода (``PeriodSource``): понедельник недели момента ``at`` в ``timezone``.

        Raises:
            ValueError: ``at`` без часового пояса.
        """
        day = _local_date(at, typed(params, BracketParams).timezone)
        return day - timedelta(days=day.weekday())

    def theme_query(
        self, params: BaseModel, *, domain: str, playable_for: PlayableFor | None
    ) -> SubjectQuery:
        """Из каких персон генератор выбирает тему периода (``subject``)."""
        p = typed(params, BracketParams)
        return SubjectQuery(
            domain=domain,
            roles=frozenset(p.subject.roles),
            min_items=p.subject.min_items,
            playable_for=playable_for,
        )
