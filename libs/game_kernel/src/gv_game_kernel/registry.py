"""Реестр плагинов и сборка движка режима из ``ModeConfig``.

``PluginRegistry`` наполняется при старте процесса (``register_builtin`` потока A + плагины
сервиса); повторная регистрация ключа — ошибка программиста, а не тихая подмена.
``EngineFactory.validate`` собирает ВСЕ проблемы конфига с JSON-путями — их показывает
редактор в админке; ``build`` отказывается собирать движок из невалидного конфига.

Правила ``validate`` (план этапа 2, §4): ключи есть в реестре; ``params`` проходят
``params_schema`` плагина; ``domain ∈ clue.domains``; ``answer.format`` поддерживается
загадкой; ``session.kind`` оркеструется сервисом; правила ``partial`` применимы к домену и
не повторяются; перекрёстные правила плагинов (``Source.cross_check``,
``Scorer.cross_check``: ``len(points) == len(levels)``, ``size == rounds`` …).

Правила этапа 3a (ADR 0023, 0026) — по возможностям, которые объявляют сами плагины:
``answer.format`` поддерживают загадка, источник (``Source.answer_formats``) и matcher
(``AnswerMatcher.answer_formats``); matcher сравнения требует ``ComparisonSource``;
формат с одним ответом (``AnswerFormat.single_answer``) — один уровень, одна попытка и
без ``partial``; Scorer с ``requires_early_end`` (серия) — правило ``EndRule.ends_early``;
``clue.reveal`` — известная загадка того же домена.

Правило этапа 3b (ADR 0028): у источника из сборника (``CollectionBasedSource``) число
раундов зависит от сессии, поэтому ``features.records`` запрещён; формат ответа — только
тот, что объявляет источник (у ``collection`` — ``classic``), варианты ``round_options`` —
не больше ``session.rounds`` (``Source.cross_check``).

Правила этапа 4 (ADR 0032) — для онлайн-сессии (``duel``/``party``/``lobby``):
``session.contest`` — известный ``ContestRule`` с валидными параметрами, совместимый с
``answer.format`` (``ContestRule.answer_formats``); ``players.min ≤ players.max``;
``len(level_windows_s)`` равно числу уровней загадки; ``ranked`` требует ``ladder``;
``lobby`` задан тогда и только тогда, когда ``kind: lobby``; свободный чат — только у
лобби; ``lobby.overridable`` — значение шаблона входит в каждый список, варианты
``answer_format`` и ``clue`` сами валидны для режима, ``players_max ≥ players.min``.
Формат с одним ответом — одна попытка на уровень у ``ContestRule``. Поля ``end`` и
``allow_guest`` у онлайн-сессии запрещены схемой.

Какие виды сессий допустимы, решает сервис (``supported_sessions``): game публикует и
соло-, и онлайн-конфиги (``ALL_SESSIONS``), играет только соло; match — только онлайн
(``ONLINE_SESSIONS``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from gv_game_kernel.engine import ModeEngine
from gv_game_kernel.errors import ConfigError
from gv_game_kernel.plugins import (
    AnswerFormat,
    AnswerMatcher,
    ClueType,
    CollectionBasedSource,
    ComparisonSource,
    ContestRule,
    DistractorSource,
    EndRule,
    PartialRule,
    RoundSource,
    Scorer,
    Source,
)
from gv_game_kernel.spec import (
    ClueSpec,
    ModeConfig,
    OnlineSessionSpec,
    SoloSessionSpec,
    error_path,
    solo_end,
)
from gv_text import TitleNormalizer

UNKNOWN_PLUGIN = "config.unknown_plugin"
"""Код проблемы: ключа плагина нет в реестре."""

DOMAIN_UNSUPPORTED = "config.domain_unsupported"
"""Код проблемы: загадка не применима к домену режима."""

ANSWER_FORMAT_UNSUPPORTED = "config.answer_format_unsupported"
"""Код проблемы: загадка не поддерживает формат ответа."""

SESSION_UNSUPPORTED = "config.session_unsupported"
"""Код проблемы: сервис не оркестрирует такой вид сессии."""

PARTIAL_DOMAIN_UNSUPPORTED = "config.partial_domain_unsupported"
"""Код проблемы: правило частичного ответа не применимо к домену."""

DUPLICATE_PARTIAL = "config.duplicate_partial"
"""Код проблемы: правило частичного ответа указано дважды."""

COMPARISON_REQUIRED = "config.comparison_required"
"""Код проблемы: matcher сравнивает по метрике, а источник не объявляет сравнения."""

END_RULE_REQUIRED = "config.end_rule_required"
"""Код проблемы: Scorer (серия) требует правила досрочного завершения (``lives``)."""

SINGLE_ANSWER_LEVELS = "config.single_answer_levels"
"""Код проблемы: формат с одним ответом (``pick``) требует один уровень и одну попытку."""

PARTIAL_UNSUPPORTED = "config.partial_unsupported"
"""Код проблемы: частичный ответ при формате с одним ответом не имеет смысла."""

RECORDS_UNSUPPORTED = "config.records_unsupported"
"""Код проблемы: личные рекорды несравнимы у режима, где раунды выбирает игрок (сборник)."""

RECORDS_FEATURE = "records"
"""Флаг ``features.records`` — личные рекорды и процентиль режима (ADR 0027)."""

PLAYERS_RANGE = "config.players_range"
"""Код проблемы: ``players.min`` больше максимума (``players.max`` или варианта лобби)."""

LEVELS_MISMATCH = "config.levels_mismatch"
"""Код проблемы: окон уровней (``level_windows_s``) не столько, сколько уровней загадки."""

LADDER_REQUIRED = "config.ladder_required"
"""Код проблемы: рейтинговому режиму (``ranked``) нужен ``ladder``."""

LOBBY_REQUIRED = "config.lobby_required"
"""Код проблемы: у ``kind: lobby`` нет настроек ``lobby``."""

LOBBY_UNEXPECTED = "config.lobby_unexpected"
"""Код проблемы: настройки ``lobby`` у сессии другого вида."""

FREE_TEXT_LOBBY_ONLY = "config.free_text_lobby_only"
"""Код проблемы: свободный чат разрешён только в кастомном лобби (ADR 0036)."""

OVERRIDE_DEFAULT_MISSING = "config.override_default_missing"
"""Код проблемы: значения шаблона нет среди вариантов ``lobby.overridable``."""

OPTIONS_SOURCE_MISSING = "config.options_source_missing"
"""Код проблемы: формат с вариантами (``pick``), а варианты никто не строит — источник не
составляет раунды сам и ``answer.params.distractors`` не задан (ADR 0040)."""

DISTRACTORS_UNSUPPORTED_SOURCE = "config.distractors_unsupported_source"
"""Код проблемы: дистракторы заданы при источнике, который сам составляет варианты."""

DISTRACTORS_FORMAT = "config.distractors_format"
"""Код проблемы: ``answer.params.distractors`` задан при формате без вариантов (не ``pick``)."""

SOLO_SESSIONS = frozenset({"solo"})
"""Виды сессий, которые играет game."""

ONLINE_SESSIONS = frozenset({"duel", "party", "lobby"})
"""Виды онлайн-сессий: их играет match (ADR 0032)."""

ALL_SESSIONS = SOLO_SESSIONS | ONLINE_SESSIONS
"""Все виды сессий: game валидирует и публикует и соло-, и онлайн-конфиги."""

DEFAULT_END_RULE = "all_rounds"
"""Правило завершения, если ``session.end`` не задан (ADR 0023)."""


class DuplicatePluginError(ValueError):
    """Плагин с таким ключом уже зарегистрирован (ошибка сборки процесса)."""


@dataclass(frozen=True, slots=True)
class ConfigIssue:
    """Проблема конфига режима.

    Attributes:
        path: JSON-путь поля (``source.type``, ``scoring.params.points``).
        code: Машинный код (``config.unknown_plugin``, ``schema.<тип>`` …); тексты для
            пользователя — по ключу i18n на клиенте.
    """

    path: str
    code: str


class PluginRegistry:
    """Реестр плагинов по видам: загадки, источники, matcher-ы, частичные ответы, очки."""

    def __init__(self) -> None:
        self._clues: dict[str, ClueType] = {}
        self._sources: dict[str, Source] = {}
        self._matchers: dict[str, AnswerMatcher] = {}
        self._partials: dict[str, PartialRule] = {}
        self._scorers: dict[str, Scorer] = {}
        self._formats: dict[str, AnswerFormat] = {}
        self._end_rules: dict[str, EndRule] = {}
        self._contests: dict[str, ContestRule] = {}
        self._distractors: dict[str, DistractorSource] = {}

    @staticmethod
    def _add[P](bucket: dict[str, P], key: str, plugin: P, kind: str) -> None:
        if key in bucket:
            raise DuplicatePluginError(f"{kind} {key!r} уже зарегистрирован")
        bucket[key] = plugin

    def register_clue(self, plugin: ClueType) -> None:
        """Зарегистрировать тип загадки. Raises: DuplicatePluginError."""
        self._add(self._clues, plugin.key, plugin, "ClueType")

    def register_source(self, plugin: Source) -> None:
        """Зарегистрировать источник. Raises: DuplicatePluginError."""
        self._add(self._sources, plugin.key, plugin, "Source")

    def register_matcher(self, plugin: AnswerMatcher) -> None:
        """Зарегистрировать matcher. Raises: DuplicatePluginError."""
        self._add(self._matchers, plugin.key, plugin, "AnswerMatcher")

    def register_partial(self, plugin: PartialRule) -> None:
        """Зарегистрировать правило частичного ответа. Raises: DuplicatePluginError."""
        self._add(self._partials, plugin.kind, plugin, "PartialRule")

    def register_scorer(self, plugin: Scorer) -> None:
        """Зарегистрировать подсчёт очков. Raises: DuplicatePluginError."""
        self._add(self._scorers, plugin.key, plugin, "Scorer")

    def register_answer_format(self, plugin: AnswerFormat) -> None:
        """Зарегистрировать формат ответа. Raises: DuplicatePluginError."""
        self._add(self._formats, plugin.key, plugin, "AnswerFormat")

    def register_end_rule(self, plugin: EndRule) -> None:
        """Зарегистрировать правило завершения сессии. Raises: DuplicatePluginError."""
        self._add(self._end_rules, plugin.key, plugin, "EndRule")

    def register_contest(self, plugin: ContestRule) -> None:
        """Зарегистрировать правило состязания матча. Raises: DuplicatePluginError."""
        self._add(self._contests, plugin.key, plugin, "ContestRule")

    def register_distractors(self, plugin: DistractorSource) -> None:
        """Зарегистрировать источник дистракторов. Raises: DuplicatePluginError."""
        self._add(self._distractors, plugin.key, plugin, "DistractorSource")

    def distractor(self, key: str) -> DistractorSource | None:
        """Источник дистракторов по ключу или ``None``."""
        return self._distractors.get(key)

    def contest(self, key: str) -> ContestRule | None:
        """Правило состязания по ключу или ``None``."""
        return self._contests.get(key)

    def answer_format(self, key: str) -> AnswerFormat | None:
        """Формат ответа по ключу или ``None``."""
        return self._formats.get(key)

    def end_rule(self, key: str) -> EndRule | None:
        """Правило завершения по ключу или ``None``."""
        return self._end_rules.get(key)

    def clue(self, key: str) -> ClueType | None:
        """Тип загадки по ключу или ``None``."""
        return self._clues.get(key)

    def source(self, key: str) -> Source | None:
        """Источник по ключу или ``None``."""
        return self._sources.get(key)

    def matcher(self, key: str) -> AnswerMatcher | None:
        """Matcher по ключу или ``None``."""
        return self._matchers.get(key)

    def partial(self, kind: str) -> PartialRule | None:
        """Правило частичного ответа по ключу или ``None``."""
        return self._partials.get(kind)

    def scorer(self, key: str) -> Scorer | None:
        """Подсчёт очков по ключу или ``None``."""
        return self._scorers.get(key)


class EngineFactory:
    """Проверка конфигов и сборка ``ModeEngine``.

    Args:
        registry: Реестр плагинов процесса.
        supported_sessions: Виды сессий, которые принимает сервис: ``SOLO_SESSIONS``
            (по умолчанию), ``ONLINE_SESSIONS`` (match) или ``ALL_SESSIONS`` (game при
            публикации конфигов).
        normalizer: Нормализатор ответов для собранных движков (по умолчанию —
            ``engine.answer_normalizer()``).
    """

    def __init__(
        self,
        registry: PluginRegistry,
        *,
        supported_sessions: frozenset[str] = SOLO_SESSIONS,
        normalizer: TitleNormalizer | None = None,
    ) -> None:
        self._registry = registry
        self._supported_sessions = supported_sessions
        self._normalizer = normalizer

    def validate(self, config: ModeConfig) -> list[ConfigIssue]:
        """Все проблемы конфига (пустой список — конфиг валиден).

        Проверки, зависящие от плагина, выполняются, только если он найден, а
        перекрёстные — только если параметры всех участников прошли свои схемы: иначе
        сравнивать не с чем, а ошибка схемы уже в списке.
        """
        r = self._registry
        clue = r.clue(config.clue.type)
        source = r.source(config.source.type)
        matcher = r.matcher(config.answer.matcher.type)
        scorer = r.scorer(config.scoring.type)
        answer_format = r.answer_format(config.answer.format)
        end_spec = solo_end(config)
        end_rule = r.end_rule(end_spec.type) if end_spec is not None else None
        reveal_spec = config.clue.reveal
        reveal = r.clue(reveal_spec.type) if reveal_spec is not None else None
        issues = [
            ConfigIssue(path, UNKNOWN_PLUGIN)
            for path, found in (
                ("source.type", source),
                ("clue.type", clue),
                ("answer.format", answer_format),
                ("answer.matcher.type", matcher),
                ("scoring.type", scorer),
            )
            if found is None
        ]
        if end_spec is not None and end_rule is None:
            issues.append(ConfigIssue("session.end.type", UNKNOWN_PLUGIN))
        if reveal_spec is not None and reveal is None:
            issues.append(ConfigIssue("clue.reveal.type", UNKNOWN_PLUGIN))
        issues.extend(self._partial_issues(config))
        issues.extend(self._distractor_issues(config, answer_format, source))

        clue_params = _parse(clue, config.clue.params, "clue.params", issues)
        source_params = _parse(source, config.source.params, "source.params", issues)
        _parse(matcher, config.answer.matcher.params, "answer.matcher.params", issues)
        scorer_params = _parse(scorer, config.scoring.params, "scoring.params", issues)
        if end_spec is not None:
            _parse(end_rule, end_spec.params, "session.end.params", issues)
        if reveal_spec is not None:
            _parse(reveal, reveal_spec.params, "clue.reveal.params", issues)
            if reveal is not None and config.domain not in reveal.domains:
                issues.append(ConfigIssue("clue.reveal.type", DOMAIN_UNSUPPORTED))

        if config.session.kind not in self._supported_sessions:
            issues.append(ConfigIssue("session.kind", SESSION_UNSUPPORTED))
        issues.extend(
            _compatibility_issues(
                config,
                clue=clue,
                source=source,
                matcher=matcher,
                scorer=scorer,
                answer_format=answer_format,
                end_rule=end_rule if end_spec is not None else r.end_rule(DEFAULT_END_RULE),
            )
        )

        levels_count = (
            len(clue.levels(clue_params)) if clue is not None and clue_params is not None else None
        )
        if isinstance(config.session, OnlineSessionSpec):
            issues.extend(self._online_issues(config, config.session, levels_count))

        if levels_count is not None:
            if answer_format is not None and answer_format.single_answer:
                issues.extend(_single_answer_issues(config, levels_count))
            for plugin, params, prefix in (
                (source, source_params, "source.params"),
                (scorer, scorer_params, "scoring.params"),
            ):
                if plugin is None or params is None:
                    continue
                issues.extend(
                    ConfigIssue(_join(prefix, i.path), i.code)
                    for i in plugin.cross_check(params, config=config, levels_count=levels_count)
                )
        return issues

    def _online_issues(
        self, config: ModeConfig, session: OnlineSessionSpec, levels_count: int | None
    ) -> list[ConfigIssue]:
        """Правила онлайн-сессии (ADR 0032): состязание, игроки, окна, ladder, лобби, чат."""
        r = self._registry
        contest = r.contest(session.contest.type)
        issues: list[ConfigIssue] = []
        if contest is None:
            issues.append(ConfigIssue("session.contest.type", UNKNOWN_PLUGIN))
        else:
            if config.answer.format not in contest.answer_formats:
                issues.append(ConfigIssue("session.contest.type", ANSWER_FORMAT_UNSUPPORTED))
            params = _parse(contest, session.contest.params, "session.contest.params", issues)
            fmt = r.answer_format(config.answer.format)
            if (
                params is not None
                and fmt is not None
                and fmt.single_answer
                and contest.round_rules(params).attempts_per_level != 1
            ):
                issues.append(
                    ConfigIssue("session.contest.params.attempts_per_level", SINGLE_ANSWER_LEVELS)
                )
        if session.players.min > session.players.max:
            issues.append(ConfigIssue("session.players.min", PLAYERS_RANGE))
        if levels_count is not None and len(session.level_windows_s) != levels_count:
            issues.append(ConfigIssue("session.level_windows_s", LEVELS_MISMATCH))
        if session.ranked and session.ladder is None:
            issues.append(ConfigIssue("session.ladder", LADDER_REQUIRED))
        is_lobby = session.kind == "lobby"
        if is_lobby and session.lobby is None:
            issues.append(ConfigIssue("session.lobby", LOBBY_REQUIRED))
        if not is_lobby and session.lobby is not None:
            issues.append(ConfigIssue("session.lobby", LOBBY_UNEXPECTED))
        if session.chat.free_text and not is_lobby:
            issues.append(ConfigIssue("session.chat.free_text", FREE_TEXT_LOBBY_ONLY))
        if session.lobby is not None:
            issues.extend(self._overridable_issues(config, session, levels_count))
        return issues

    def _overridable_issues(
        self, config: ModeConfig, session: OnlineSessionSpec, levels_count: int | None
    ) -> list[ConfigIssue]:
        """``lobby.overridable``: значение шаблона входит в каждый список, варианты валидны."""
        lobby = session.lobby
        if lobby is None:  # pragma: no cover — проверяет вызывающий
            return []
        over = lobby.overridable
        prefix = "session.lobby.overridable"
        windows = set(session.level_windows_s)
        # Вариант окна — одно значение на все уровни: у шаблона с разными окнами его нет.
        template_window = next(iter(windows)) if len(windows) == 1 else None
        # object: значения разных полей — числа, строки, ClueSpec; сравниваются на равенство.
        defaults: list[tuple[str, tuple[object, ...] | None, object]] = [
            ("rounds", over.rounds, session.rounds),
            ("level_window_s", over.level_window_s, template_window),
            ("answer_format", over.answer_format, config.answer.format),
            ("clue", over.clue, config.clue),
            ("players_max", over.players_max, session.players.max),
            ("visibility", over.visibility, lobby.visibility),
        ]
        issues = [
            ConfigIssue(f"{prefix}.{name}", OVERRIDE_DEFAULT_MISSING)
            for name, options, default in defaults
            if options and default not in options
        ]
        issues.extend(
            ConfigIssue(f"{prefix}.players_max[{i}]", PLAYERS_RANGE)
            for i, value in enumerate(over.players_max or ())
            if value < session.players.min
        )
        for i, fmt in enumerate(over.answer_format or ()):
            issues.extend(
                self._format_variant_issues(config, session, f"{prefix}.answer_format[{i}]", fmt)
            )
        for i, clue_spec in enumerate(over.clue or ()):
            issues.extend(
                self._clue_variant_issues(config, clue_spec, f"{prefix}.clue[{i}]", levels_count)
            )
        return issues

    def _format_variant_issues(
        self, config: ModeConfig, session: OnlineSessionSpec, path: str, fmt: str
    ) -> list[ConfigIssue]:
        """Вариант ``answer.format`` лобби: известен и поддержан загадкой, источником,
        matcher-ом и состязанием режима."""
        r = self._registry
        if r.answer_format(fmt) is None:
            return [ConfigIssue(path, UNKNOWN_PLUGIN)]
        clue, source = r.clue(config.clue.type), r.source(config.source.type)
        matcher, contest = r.matcher(config.answer.matcher.type), r.contest(session.contest.type)
        supported = [
            clue.supported_answer_formats if clue is not None else None,
            source.answer_formats if source is not None else None,
            matcher.answer_formats if matcher is not None else None,
            contest.answer_formats if contest is not None else None,
        ]
        if any(formats is not None and fmt not in formats for formats in supported):
            return [ConfigIssue(path, ANSWER_FORMAT_UNSUPPORTED)]
        return []

    def _clue_variant_issues(
        self, config: ModeConfig, spec: ClueSpec, path: str, levels_count: int | None
    ) -> list[ConfigIssue]:
        """Вариант загадки лобби — валидный ``clue`` того же домена с тем же числом уровней
        (окна уровней шаблона общие для всех вариантов)."""
        r = self._registry
        clue = r.clue(spec.type)
        if clue is None:
            return [ConfigIssue(f"{path}.type", UNKNOWN_PLUGIN)]
        issues: list[ConfigIssue] = []
        if config.domain not in clue.domains:
            issues.append(ConfigIssue(f"{path}.type", DOMAIN_UNSUPPORTED))
        if config.answer.format not in clue.supported_answer_formats:
            issues.append(ConfigIssue(f"{path}.type", ANSWER_FORMAT_UNSUPPORTED))
        params = _parse(clue, spec.params, f"{path}.params", issues)
        if (
            params is not None
            and levels_count is not None
            and len(clue.levels(params)) != levels_count
        ):
            issues.append(ConfigIssue(f"{path}.params.levels", LEVELS_MISMATCH))
        if spec.reveal is not None:
            reveal = r.clue(spec.reveal.type)
            if reveal is None:
                issues.append(ConfigIssue(f"{path}.reveal.type", UNKNOWN_PLUGIN))
            else:
                _parse(reveal, spec.reveal.params, f"{path}.reveal.params", issues)
                if config.domain not in reveal.domains:
                    issues.append(ConfigIssue(f"{path}.reveal.type", DOMAIN_UNSUPPORTED))
        return issues

    def _partial_issues(self, config: ModeConfig) -> list[ConfigIssue]:
        issues: list[ConfigIssue] = []
        seen: set[str] = set()
        for i, kind in enumerate(config.answer.partial):
            path = f"answer.partial[{i}]"
            rule = self._registry.partial(kind)
            if rule is None:
                issues.append(ConfigIssue(path, UNKNOWN_PLUGIN))
            elif config.domain not in rule.domains:
                issues.append(ConfigIssue(path, PARTIAL_DOMAIN_UNSUPPORTED))
            if kind in seen:
                issues.append(ConfigIssue(path, DUPLICATE_PARTIAL))
            seen.add(kind)
        return issues

    def _distractor_issues(
        self, config: ModeConfig, answer_format: AnswerFormat | None, source: Source | None
    ) -> list[ConfigIssue]:
        """``answer.params.distractors`` (ADR 0040): плагин известен, параметры верны,
        формат даёт варианты (``needs_options``), ``min_pool ≥ count + 1`` (``cross_check``)."""
        spec = config.answer.params.distractors if config.answer.params is not None else None
        base = "answer.params.distractors"
        builds_options = isinstance(source, RoundSource)
        if spec is None:
            needs = answer_format is not None and answer_format.needs_options
            if needs and source is not None and not builds_options:
                return [ConfigIssue(base, OPTIONS_SOURCE_MISSING)]
            return []
        issues: list[ConfigIssue] = []
        if builds_options:
            issues.append(ConfigIssue(base, DISTRACTORS_UNSUPPORTED_SOURCE))
        if answer_format is not None and not answer_format.needs_options:
            issues.append(ConfigIssue(base, DISTRACTORS_FORMAT))
        plugin = self._registry.distractor(spec.type)
        if plugin is None:
            issues.append(ConfigIssue(f"{base}.type", UNKNOWN_PLUGIN))
            return issues
        params = _parse(plugin, spec.params, f"{base}.params", issues)
        if params is not None:
            issues.extend(
                ConfigIssue(_join(f"{base}.params", i.path), i.code)
                for i in plugin.cross_check(params, count=spec.count)
            )
        return issues

    def build(self, config: ModeConfig) -> ModeEngine:
        """Собрать движок режима.

        Raises:
            ConfigError: конфиг невалиден; ``details["errors"]`` — ``[{path, code}]``.
        """
        issues = self.validate(config)
        if issues:
            raise ConfigError(
                details={"errors": [{"path": i.path, "code": i.code} for i in issues]}
            )
        r = self._registry
        clue, source = r.clue(config.clue.type), r.source(config.source.type)
        matcher, scorer = r.matcher(config.answer.matcher.type), r.scorer(config.scoring.type)
        answer_format = r.answer_format(config.answer.format)
        end_spec = solo_end(config)
        end_rule = r.end_rule(end_spec.type if end_spec is not None else DEFAULT_END_RULE)
        reveal = r.clue(config.clue.reveal.type) if config.clue.reveal else None
        partials = tuple(r.partial(kind) for kind in config.answer.partial)
        session = config.session
        distractor_spec = config.answer.params.distractors if config.answer.params else None
        distractors = r.distractor(distractor_spec.type) if distractor_spec is not None else None
        contest = (
            r.contest(session.contest.type) if isinstance(session, OnlineSessionSpec) else None
        )
        if (
            clue is None
            or source is None
            or matcher is None
            or scorer is None
            or answer_format is None
            or end_rule is None
        ):
            raise AssertionError("validate пропустил неизвестный плагин")  # pragma: no cover
        return ModeEngine(
            config,
            clue=clue,
            source=source,
            matcher=matcher,
            partials=tuple(p for p in partials if p is not None),
            scorer=scorer,
            answer_format=answer_format,
            end_rule=end_rule,
            reveal=reveal,
            normalizer=self._normalizer,
            contest=contest,
            distractors=distractors,
        )


def _compatibility_issues(
    config: ModeConfig,
    *,
    clue: ClueType | None,
    source: Source | None,
    matcher: AnswerMatcher | None,
    scorer: Scorer | None,
    answer_format: AnswerFormat | None,
    end_rule: EndRule | None,
) -> list[ConfigIssue]:
    """Совместимость плагинов по объявленным ими возможностям (не по ключам)."""
    issues: list[ConfigIssue] = []
    fmt = config.answer.format
    if clue is not None:
        if config.domain not in clue.domains:
            issues.append(ConfigIssue("domain", DOMAIN_UNSUPPORTED))
        if fmt not in clue.supported_answer_formats:
            issues.append(ConfigIssue("answer.format", ANSWER_FORMAT_UNSUPPORTED))
    if (
        scorer is not None
        and scorer.requires_early_end
        and (end_rule is None or not end_rule.ends_early)
    ):
        issues.append(ConfigIssue("session.end", END_RULE_REQUIRED))
    if answer_format is not None:
        if source is not None and fmt not in source.answer_formats:
            issues.append(ConfigIssue("source.type", ANSWER_FORMAT_UNSUPPORTED))
        if matcher is not None and fmt not in matcher.answer_formats:
            issues.append(ConfigIssue("answer.matcher.type", ANSWER_FORMAT_UNSUPPORTED))
    if (
        matcher is not None
        and source is not None
        and matcher.requires_comparison
        and not isinstance(source, ComparisonSource)
    ):
        issues.append(ConfigIssue("answer.matcher.type", COMPARISON_REQUIRED))
    if answer_format is not None and answer_format.single_answer and config.answer.partial:
        issues.append(ConfigIssue("answer.partial", PARTIAL_UNSUPPORTED))
    if (
        source is not None
        and isinstance(source, CollectionBasedSource)
        and config.features.get(RECORDS_FEATURE)
    ):
        issues.append(ConfigIssue(f"features.{RECORDS_FEATURE}", RECORDS_UNSUPPORTED))
    return issues


def _single_answer_issues(config: ModeConfig, levels_count: int) -> list[ConfigIssue]:
    """Формат с одним ответом на раунд: один уровень загадки и одна попытка."""
    issues: list[ConfigIssue] = []
    if levels_count != 1:
        issues.append(ConfigIssue("clue.params.levels", SINGLE_ANSWER_LEVELS))
    if isinstance(config.session, SoloSessionSpec) and config.session.attempts_per_level != 1:
        issues.append(ConfigIssue("session.attempts_per_level", SINGLE_ANSWER_LEVELS))
    return issues


def _parse(
    plugin: ClueType
    | Source
    | AnswerMatcher
    | Scorer
    | EndRule
    | ContestRule
    | DistractorSource
    | None,
    params: Mapping[str, Any],  # Any: params плагина — произвольный JSON-объект
    prefix: str,
    issues: list[ConfigIssue],
) -> BaseModel | None:
    """Разобрать ``params`` схемой плагина; ошибки — в ``issues`` с путём от корня."""
    if plugin is None:
        return None
    try:
        return plugin.params_schema.model_validate(dict(params))
    except ValidationError as exc:
        issues.extend(
            ConfigIssue(_join(prefix, error_path(tuple(e["loc"]))), f"schema.{e['type']}")
            for e in exc.errors(include_url=False, include_input=False)
        )
        return None


def _join(prefix: str, path: str) -> str:
    """``scoring.params`` + ``points`` → ``scoring.params.points``; ``$`` — сам префикс."""
    if path == "$":
        return prefix
    return f"{prefix}{path}" if path.startswith("[") else f"{prefix}.{path}"


__all__ = [
    "ALL_SESSIONS",
    "ANSWER_FORMAT_UNSUPPORTED",
    "COMPARISON_REQUIRED",
    "DEFAULT_END_RULE",
    "DISTRACTORS_FORMAT",
    "DISTRACTORS_UNSUPPORTED_SOURCE",
    "DOMAIN_UNSUPPORTED",
    "DUPLICATE_PARTIAL",
    "END_RULE_REQUIRED",
    "FREE_TEXT_LOBBY_ONLY",
    "LADDER_REQUIRED",
    "LEVELS_MISMATCH",
    "LOBBY_REQUIRED",
    "LOBBY_UNEXPECTED",
    "ONLINE_SESSIONS",
    "OPTIONS_SOURCE_MISSING",
    "OVERRIDE_DEFAULT_MISSING",
    "PARTIAL_DOMAIN_UNSUPPORTED",
    "PARTIAL_UNSUPPORTED",
    "PLAYERS_RANGE",
    "RECORDS_FEATURE",
    "RECORDS_UNSUPPORTED",
    "SESSION_UNSUPPORTED",
    "SINGLE_ANSWER_LEVELS",
    "SOLO_SESSIONS",
    "UNKNOWN_PLUGIN",
    "ConfigIssue",
    "DuplicatePluginError",
    "EngineFactory",
    "ModeEngine",
    "PluginRegistry",
]
