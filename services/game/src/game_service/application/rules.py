"""Правила режима для game: фасад над ``ModeEngine`` ядра.

game не знает про конкретные плагины: ни ``if type == ...``, ни разбора ``params`` по
ключу плагина. Возможности режима game узнаёт у ядра — по протоколам источника
(``RoundSource``, ``ProgressiveSource``, ``SubjectSource``, ``PeriodSource``; ADR 0023) и
атрибутам плагинов (``Scorer.presentation``, ``clue.reveal``). Из параметров источника
game читает только общие поля плановых наборов (возможности, а не ключи):

* ``timezone`` — зона смены периода (у ``daily`` и ``bracket``);
* ``schedule_ahead_days`` | ``schedule_ahead_periods`` — на сколько периодов вперёд
  создавать наборы;
* ``exclude_recent_days`` — сколько дней не повторять единицы (нет — 0);
* ``period`` — имя периода (нет — ``day``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from game_service.domain.errors import ModeUnavailableError
from game_service.domain.modes import ConfigVersion
from gv_core.errors import AppError
from gv_game_kernel import (
    AssetPort,
    AssetRef,
    ClueView,
    ConfigError,
    ConfigIssue,
    EngineFactory,
    LevelSpec,
    ModeConfig,
    ModeEngine,
    PeriodSource,
    PlayableFor,
    PluginRegistry,
    PoolQuery,
    RoundOutcome,
    RoundState,
    SubjectQuery,
    params_hash,
    parse_mode_config,
)

POOL_TOO_SMALL = "game.pool_too_small"
"""Код ошибки ядра: в пуле меньше единиц, чем нужно (``PoolTooSmallError`` потока A)."""

DEFAULT_PERIOD = "day"
MAX_PERIOD_DAYS = 31
"""Самый длинный период, который ищет ``next_period_start`` (неделя, месяц)."""

RECORDS_FEATURE = "records"
"""Флаг ``features.records``: личные рекорды и процентиль (D11)."""


@runtime_checkable
class _Zoned(Protocol):
    timezone: str


@runtime_checkable
class _AheadDays(Protocol):
    schedule_ahead_days: int


@runtime_checkable
class _AheadPeriods(Protocol):
    schedule_ahead_periods: int


@runtime_checkable
class _ExcludeRecent(Protocol):
    exclude_recent_days: int


@runtime_checkable
class _Periodic(Protocol):
    period: str


@dataclass(frozen=True, slots=True)
class PeriodSchedule:
    """Плановые наборы режима (дейлик — день, сетка — неделя; ADR 0027).

    Attributes:
        period: Имя периода (``day`` | ``week``) — ``daily_sets.period``.
        timezone: IANA-зона смены периода.
        ahead: На сколько периодов вперёд создавать наборы.
        exclude_recent_days: Сколько последних дней не повторять единицы.
    """

    period: str
    timezone: str
    ahead: int
    exclude_recent_days: int


@dataclass(frozen=True, slots=True)
class PresetView:
    """Пресет случайного режима."""

    id: str
    title_key: str


def _issue(path: str, code: str) -> dict[str, str]:
    return {"path": path, "code": code}


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
    def answer_format(self) -> str:
        """Ключ формата ответа (``classic`` | ``pick``)."""
        return self.config.answer.format

    @property
    def presentation(self) -> str:
        """Подача итога (``Scorer.presentation``): ``score`` | ``streak`` | ``bracket``."""
        return self.engine.presentation

    @property
    def records_enabled(self) -> bool:
        """Личные рекорды режима включены флагом ``features.records`` (D11)."""
        return bool(self.config.features.get(RECORDS_FEATURE, False))

    @property
    def is_progressive(self) -> bool:
        """Раунды зависят от прошлых (сетка): туры дописываются ``followups``."""
        return self.engine.is_progressive

    @property
    def rounds(self) -> int:
        """Раундов в сессии самое большее (сетка — ``size - 1``; с ``session.end`` —
        максимум, правило может закончить раньше)."""
        return self.engine.planned_rounds()

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
        return self.engine.round_max_score()

    @property
    def playable_for(self) -> PlayableFor:
        """Требование играбельности единиц (готовые ассеты под ``clue.params``)."""
        return self.engine.playable_for

    @property
    def reveal_playable_for(self) -> PlayableFor | None:
        """Второй набор ассетов ревила другого типа (ADR 0026) или ``None``."""
        return self.engine.reveal_playable_for

    # ------------------------------------------------------------------ плановые наборы
    @property
    def schedule(self) -> PeriodSchedule | None:
        """Плановые наборы, если источник их объявляет (``PeriodSource`` ядра)."""
        if not isinstance(self.engine.source, PeriodSource):
            return None
        params = self.engine.source_params
        if not isinstance(params, _Zoned):
            return None
        if isinstance(params, _AheadPeriods):
            ahead = params.schedule_ahead_periods
        elif isinstance(params, _AheadDays):
            ahead = params.schedule_ahead_days
        else:
            ahead = 0
        return PeriodSchedule(
            period=params.period if isinstance(params, _Periodic) else DEFAULT_PERIOD,
            timezone=params.timezone,
            ahead=ahead,
            exclude_recent_days=(
                params.exclude_recent_days if isinstance(params, _ExcludeRecent) else 0
            ),
        )

    def period_start(self, at: datetime) -> date:
        """Начало периода момента ``at`` (``day`` набора).

        Raises:
            ModeUnavailableError: у режима нет плановых наборов (ошибка вызова).
        """
        start = self.engine.period_start(at)
        if start is None:
            raise ModeUnavailableError("у режима нет плановых наборов")
        return start

    def next_period_start(self, start: date) -> date:
        """Начало периода, следующего за периодом ``start``.

        Период ищется через ``period_start`` ядра: game не знает его длину.

        Raises:
            ModeUnavailableError: нет плановых наборов или период длиннее ``MAX_PERIOD_DAYS``.
        """
        schedule = self.schedule
        if schedule is None:
            raise ModeUnavailableError("у режима нет плановых наборов")
        zone = ZoneInfo(schedule.timezone)
        for offset in range(1, MAX_PERIOD_DAYS + 1):
            day = start + timedelta(days=offset)
            following = self.period_start(datetime.combine(day, time(12), tzinfo=zone))
            if following != start:
                return following
        raise ModeUnavailableError("период режима длиннее допустимого")

    def next_reset_at(self, at: datetime) -> datetime:
        """Момент смены периода после ``at`` (00:00 следующего периода в зоне, UTC)."""
        schedule = self.schedule
        if schedule is None:
            raise ModeUnavailableError("у режима нет плановых наборов")
        following = self.next_period_start(self.period_start(at))
        zone = ZoneInfo(schedule.timezone)
        return datetime.combine(following, time.min, tzinfo=zone).astimezone(UTC)

    def theme_query(self) -> SubjectQuery | None:
        """Из каких персон выбирать тему периода (сетка) или ``None``."""
        return self.engine.theme_query()

    # ------------------------------------------------------------------ старт
    def presets(self) -> list[PresetView]:
        """Пресеты источника (пусто — их нет)."""
        return [PresetView(p.id, p.title_key) for p in self.engine.presets()]

    def subject_query(self) -> SubjectQuery | None:
        """Персоны, из которых игрок выбирает (марафон), или ``None``."""
        return self.engine.subject_query()

    def max_subjects(self) -> int | None:
        """Сколько персон можно выбрать или ``None``."""
        return self.engine.max_subjects()

    def pool_query(self) -> PoolQuery | None:
        """Запрос играбельного пула режима (без пресета) для проверки при публикации."""
        return self.engine.pool_query()

    def min_playable(self) -> int:
        """Минимум играбельных единиц для публикации."""
        return self.engine.min_playable()

    # ------------------------------------------------------------------ правила раунда
    def skip(self, state: RoundState) -> RoundState:
        """Переход раунда после пропуска."""
        return self.engine.skip(state)

    def give_up(self, state: RoundState) -> RoundState:
        """Сдаться."""
        return self.engine.give_up(state)

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

    async def final_view(
        self,
        asset: AssetRef,
        *,
        assets: AssetPort,
        subject: str,
        reveal_asset: AssetRef | None = None,
    ) -> ClueView:
        """Подача для ревила завершённого раунда (``clue.reveal`` — набор ``reveal_asset``)."""
        return await self.engine.final_view(
            asset, assets=assets, subject=subject, reveal_asset=reveal_asset
        )

    async def shown_view(self, asset: AssetRef, *, assets: AssetPort, subject: str) -> ClueView:
        """Подача открыто показанной единицы: последний уровень самой загадки режима."""
        return await self.engine.clue.final_view(
            asset, self.engine.clue_params, assets=assets, subject=subject
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

    def clue_params(self, config: ModeConfig) -> list[tuple[str, str, BaseModel]]:
        """Параметры загадки и ревила, разобранные схемами плагинов: ``[(путь, тип, params)]``.

        Raises:
            ConfigError: плагина нет или параметры не проходят его схему (пути
                ``clue.params…`` / ``clue.reveal.params…``).
        """
        specs = [("clue", config.clue.type, config.clue.params)]
        if config.clue.reveal is not None:
            specs.append(("clue.reveal", config.clue.reveal.type, config.clue.reveal.params))
        parsed: list[tuple[str, str, BaseModel]] = []
        for path, clue_type, params in specs:
            plugin = self.registry.clue(clue_type)
            if plugin is None:
                raise ConfigError(
                    details={"errors": [_issue(f"{path}.type", "config.unknown_plugin")]}
                )
            try:
                parsed.append((path, clue_type, plugin.params_schema.model_validate(params)))
            except ValueError as exc:
                raise ConfigError(
                    details={"errors": [_issue(f"{path}.params", "schema.invalid")]}
                ) from exc
        return parsed

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
