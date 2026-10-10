"""Встроенные источники единиц раундов: ``daily``, ``random``, ``collection``.

Розыгрыш детерминирован: кандидаты сортируются по ``id``, затем
``SeededRandom(ctx.seed).sample``. Одинаковые seed и пул дают одинаковый набор, а без
секрета seed дейлика (``daily_seed``) набор не предсказать.

``collection`` реализован на ``CollectionPort``, но в game на этапе 2 не регистрируется
(проекции сборников — этап 3): ``register_builtin`` его не включает.
"""

# ruff: noqa: ARG002 — методы реализуют протоколы плагинов, не всем нужны все аргументы

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence
from datetime import date, datetime
from typing import Annotated, ClassVar
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator

from gv_core.random import SeededRandom
from gv_game_kernel.builtin._base import PluginParams, custom_error, typed
from gv_game_kernel.builtin.pool import (
    PoolFilters,
    PoolParams,
    build_query,
    filters_issues,
    min_playable,
    min_playable_issues,
)
from gv_game_kernel.errors import PoolTooSmallError, PresetNotFoundError
from gv_game_kernel.plugins import DrawContext, ParamIssue, Preset
from gv_game_kernel.ports import PlayableFor, PoolQuery
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
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise custom_error(
                "timezone_unknown", "неизвестная IANA-зона {zone}", zone=value
            ) from exc
        return value


class DailySource:
    """Набор дня по правилам пула (D6).

    Кандидаты — играбельные под параметры загадки единицы пула (``playable_for``). Единицы
    из наборов последних ``exclude_recent_days`` дней (``ctx.exclude_ids``, их собирает
    game) исключаются; если свежих не хватает, недостающие добираются из недавних — пустой
    день хуже повтора.
    """

    key: ClassVar[str] = "daily"
    params_schema: ClassVar[type[BaseModel]] = DailyParams

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
        p = typed(params, DailyParams)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("момент должен быть с часовым поясом")
        return at.astimezone(ZoneInfo(p.timezone)).date()


def _with_exclude(query: PoolQuery, exclude: frozenset[UUID]) -> PoolQuery:
    if not exclude:
        return query
    return PoolQuery(
        domain=query.domain,
        kinds=query.kinds,
        genres_any=query.genres_any,
        year_from=query.year_from,
        year_to=query.year_to,
        min_popularity=query.min_popularity,
        playable_for=query.playable_for,
        exclude_ids=exclude,
    )


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
class CollectionParams(PluginParams):
    """Параметры ``collection`` (схема в ``contracts/modes`` — этап 3).

    Attributes:
        collection_id: Сборник.
        shuffle: Перемешать единицы (иначе — первые ``n`` в порядке сборника).
    """

    collection_id: UUID
    shuffle: StrictBool = True


class CollectionSource:
    """Единицы сборника через ``CollectionPort`` (этап 3; в game на этапе 2 не регистрируется)."""

    key: ClassVar[str] = "collection"
    params_schema: ClassVar[type[BaseModel]] = CollectionParams

    async def draw(self, ctx: DrawContext, params: BaseModel, n: int) -> list[MediaItemRef]:
        """``n`` единиц сборника (перемешанных по ``ctx.seed`` или по порядку).

        Raises:
            ValueError: в контексте нет ``CollectionPort``.
            PoolTooSmallError: в сборнике меньше ``n`` единиц.
        """
        _require_n(n)
        p = typed(params, CollectionParams)
        if ctx.collections is None:
            raise ValueError("источнику collection нужен CollectionPort")
        items = await ctx.collections.items(p.collection_id, ctx.candidate_limit)
        seen: set[UUID] = set()
        ordered: list[MediaItemRef] = []
        for item in items:
            if item.id not in seen and item.id not in ctx.exclude_ids:
                seen.add(item.id)
                ordered.append(item)
        if len(ordered) < n:
            raise _pool_too_small(len(ordered), n)
        if p.shuffle:
            return SeededRandom(ctx.seed).sample(ordered, n)
        return ordered[:n]

    def presets(self, params: BaseModel) -> list[Preset]:
        """У сборника пресетов нет."""
        typed(params, CollectionParams)
        return []

    def cross_check(
        self, params: BaseModel, *, config: ModeConfig, levels_count: int
    ) -> list[ParamIssue]:
        """Перекрёстных правил нет."""
        typed(params, CollectionParams)
        return []

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
        """Сборник должен покрывать все раунды."""
        typed(params, CollectionParams)
        return rounds
