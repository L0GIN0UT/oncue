"""Правила режима для game: фасад над ``ModeEngine`` ядра и разобранные параметры.

game не знает про конкретные плагины: ни ``if type == ...``, ни разбора ``params``
по ключу. Единственное, что game узнаёт о параметрах, — возможности, объявленные самими
моделями параметров:

* ``ScheduledSource`` — источник с наборами дня (поля ``size``, ``timezone``,
  ``schedule_ahead_days``, ``exclude_recent_days``, как в ``source.daily.v1.json``);
* ``presets`` — источник объявляет пресеты (``Source.presets``);
* ``pool`` — общий фрагмент ``contracts/modes/params/pool.v1.json`` для подсчёта пула.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from game_service.domain.errors import ModeUnavailableError
from game_service.domain.modes import ConfigVersion
from gv_core.errors import AppError
from gv_game_kernel import (
    AnswerInput,
    AssetPort,
    AssetRef,
    ClueView,
    ConfigError,
    ConfigIssue,
    DrawContext,
    EngineFactory,
    Guess,
    LevelSpec,
    MatchResult,
    MediaItemRef,
    ModeConfig,
    ModeEngine,
    PlayableFor,
    PluginRegistry,
    PoolQuery,
    RoundOutcome,
    RoundState,
    params_hash,
    parse_mode_config,
)
from gv_game_kernel import round as kernel_round
from gv_game_kernel.spec import error_path

POOL_TOO_SMALL = "game.pool_too_small"
"""Код ошибки ядра: в пуле меньше единиц, чем нужно (``PoolTooSmallError`` потока A)."""


@runtime_checkable
class ScheduledSource(Protocol):
    """Параметры источника с наборами дня (возможность, а не ключ плагина)."""

    size: int
    timezone: str
    schedule_ahead_days: int
    exclude_recent_days: int


@runtime_checkable
class _PresetSource(Protocol):
    def presets(self, params: BaseModel) -> Sequence[Any]: ...  # Preset ядра (id, title_key)


@dataclass(frozen=True, slots=True)
class PresetView:
    """Пресет случайного режима."""

    id: str
    title_key: str


class _Years(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    year_from: int | None = Field(default=None, alias="from")
    year_to: int | None = Field(default=None, alias="to")


class PoolSpec(BaseModel):
    """Фрагмент ``pool.v1.json``: правила пула (проверяет ядро, game только считает пул)."""

    model_config = ConfigDict(extra="ignore")

    genres_any: list[str] = Field(default_factory=list)
    years: _Years | None = None
    min_popularity: float | None = None
    kinds: list[str] = Field(default_factory=list)
    min_playable: int | None = None

    def query(self, domain: str, playable_for: PlayableFor | None) -> PoolQuery:
        """Запрос кандидатов пула."""
        return PoolQuery(
            domain=domain,
            kinds=frozenset(self.kinds),
            genres_any=frozenset(self.genres_any),
            year_from=self.years.year_from if self.years else None,
            year_to=self.years.year_to if self.years else None,
            min_popularity=self.min_popularity,
            playable_for=playable_for,
        )


def _issue(path: str, code: str) -> dict[str, str]:
    return {"path": path, "code": code}


def _params_issues(prefix: str, exc: ValidationError) -> list[dict[str, str]]:
    issues = []
    for e in exc.errors(include_url=False, include_input=False):
        loc = tuple(e["loc"])
        path = f"{prefix}.{error_path(loc)}" if loc else prefix
        issues.append(_issue(path, f"schema.{e['type']}"))
    return issues


def _parse_params(schema: type[BaseModel], params: Mapping[str, Any], section: str) -> BaseModel:
    """Параметры плагина; ошибки — ``ConfigError`` с путями ``<section>.params.…``.

    Валидатор ядра тоже проверяет параметры — здесь проверка на случай, если сборка
    движка их не разобрала (параметры нужны game для ревила и наборов дня).
    """
    try:
        return schema.model_validate(dict(params))
    except ValidationError as exc:
        raise ConfigError(details={"errors": _params_issues(f"{section}.params", exc)}) from exc


class ModeRules:
    """Собранный движок одного режима и версии.

    Attributes:
        key: Ключ режима.
        version: Версия конфига.
        config: Тело конфига (модель ядра).
        engine: Движок ядра.
        params_hash: ``params_hash(clue.params)`` — ключ поиска ассетов (D7).
    """

    def __init__(self, version: int, config: ModeConfig, engine: ModeEngine) -> None:
        self.key = config.key
        self.version = version
        self.config = config
        self.engine = engine
        self.params_hash = params_hash(config.clue.params)
        self._clue_params = _parse_params(engine.clue.params_schema, config.clue.params, "clue")
        self._source_params = _parse_params(
            engine.source.params_schema, config.source.params, "source"
        )
        self._levels = list(engine.levels())

    # ------------------------------------------------------------------ свойства режима
    @property
    def domain(self) -> str:
        """Домен."""
        return self.config.domain

    @property
    def clue_type(self) -> str:
        """Ключ ClueType."""
        return self.config.clue.type

    @property
    def source_type(self) -> str:
        """Ключ Source."""
        return self.config.source.type

    @property
    def rounds(self) -> int:
        """Раундов в сессии (для дейлика — ``size`` набора)."""
        schedule = self.schedule
        return schedule.size if schedule is not None else self.config.session.rounds

    @property
    def levels(self) -> list[LevelSpec]:
        """Уровни загадки."""
        return self._levels

    @property
    def levels_count(self) -> int:
        """Число уровней (= попыток, D9)."""
        return len(self._levels)

    @property
    def round_max(self) -> int:
        """Максимум очков за один раунд (Scorer)."""
        return self.engine.max_score(1)

    @property
    def schedule(self) -> ScheduledSource | None:
        """Параметры наборов дня, если источник их объявляет."""
        params = self._source_params
        return params if isinstance(params, ScheduledSource) else None

    @property
    def playable_for(self) -> PlayableFor:
        """Требование играбельности единиц (готовые ассеты под ``clue.params``)."""
        return PlayableFor(self.clue_type, self.params_hash)

    @property
    def pool(self) -> PoolSpec | None:
        """Правила пула из ``source.params.pool``."""
        raw = self.config.source.params.get("pool")
        return PoolSpec.model_validate(raw) if isinstance(raw, Mapping) else None

    def presets(self) -> list[PresetView]:
        """Пресеты источника (пусто — их нет)."""
        source = self.engine.source
        if not isinstance(source, _PresetSource):
            return []
        return [
            PresetView(str(p.id), str(p.title_key)) for p in source.presets(self._source_params)
        ]

    # ------------------------------------------------------------------ правила
    async def draw(self, ctx: DrawContext, n: int) -> list[MediaItemRef]:
        """Разыграть ``n`` единиц."""
        return await self.engine.draw(ctx, n)

    def check(self, answer: AnswerInput, item: MediaItemRef) -> MatchResult:
        """Сверить ответ."""
        return self.engine.check(answer, item)

    def guess(self, state: RoundState, guess: Guess) -> RoundState:
        """Переход раунда после ответа."""
        return kernel_round.apply_guess(
            state, guess, levels_count=self.levels_count, session=self.config.session
        )

    def skip(self, state: RoundState) -> RoundState:
        """Переход раунда после пропуска."""
        return kernel_round.apply_skip(
            state, levels_count=self.levels_count, session=self.config.session
        )

    def give_up(self, state: RoundState) -> RoundState:
        """Сдаться."""
        return kernel_round.give_up(state, session=self.config.session)

    def score(self, outcome: RoundOutcome) -> int:
        """Очки за раунд."""
        return self.engine.score(outcome)

    async def clue_view(
        self, state: RoundState, asset: AssetRef, level: int, *, assets: AssetPort, subject: str
    ) -> ClueView:
        """Подача уровня (ссылка через ``AssetPort``); ядро ещё раз проверяет, что уровень открыт.

        Raises:
            LevelLockedError: уровень выше открытого.
            GoneError: ассет выключен — раунд аннулируется (``ModeEngine.void``).
        """
        return await self.engine.clue_view(state, asset, level, assets=assets, subject=subject)

    async def final_view(self, asset: AssetRef, *, assets: AssetPort, subject: str) -> ClueView:
        """Подача для ревила завершённого раунда."""
        return await self.engine.clue.final_view(
            asset, self._clue_params, assets=assets, subject=subject
        )


@dataclass(frozen=True, slots=True)
class ConfigCheck:
    """Итог проверки тела конфига ядром.

    Attributes:
        config: Разобранный конфиг (``None`` — тело не прошло схему).
        rules: Собранные правила (``None`` — есть ошибки).
        errors: ``[{path, code}]``.
    """

    config: ModeConfig | None
    rules: ModeRules | None
    errors: list[dict[str, str]]


class RulesFactory:
    """Проверка тел конфигов и сборка ``ModeRules`` по реестру плагинов процесса."""

    def __init__(self, registry: PluginRegistry) -> None:
        self.registry = registry
        self._factory = EngineFactory(registry)
        self._cache: dict[tuple[str, int, str], ModeRules] = {}

    def check(self, body: Mapping[str, Any], *, version: int = 0) -> ConfigCheck:
        """Разобрать и проверить тело: схема, реестр, параметры плагинов, согласованность."""
        try:
            config = parse_mode_config(body)
        except ConfigError as exc:
            return ConfigCheck(None, None, list(exc.details.get("errors", [])))
        errors = [_issue(i.path, i.code) for i in self._factory.validate(config)]
        if errors:
            return ConfigCheck(config, None, errors)
        try:
            rules = ModeRules(version, config, self._factory.build(config))
        except ConfigError as exc:
            return ConfigCheck(config, None, list(exc.details.get("errors", [])))
        return ConfigCheck(config, rules, [])

    def build(self, version: ConfigVersion) -> ModeRules:
        """Правила опубликованной версии (кэш на процесс: версии неизменяемы).

        Raises:
            ModeUnavailableError: версия не собирается текущим реестром плагинов.
        """
        cache_key = (version.key, version.version, version.body_hash)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        check = self.check(version.body, version=version.version)
        if check.rules is None:
            raise ModeUnavailableError(
                "конфиг режима не собирается текущим реестром плагинов",
                details={"mode_key": version.key, "version": version.version},
            )
        self._cache[cache_key] = check.rules
        return check.rules


def issues_to_dicts(issues: Sequence[ConfigIssue]) -> list[dict[str, str]]:
    """Проблемы ядра → ``[{path, code}]``."""
    return [_issue(i.path, i.code) for i in issues]


def is_pool_too_small(exc: AppError) -> bool:
    """Ошибка ядра «пул меньше нужного» (класс ядра, код ``game.pool_too_small``)."""
    return exc.code == POOL_TOO_SMALL
