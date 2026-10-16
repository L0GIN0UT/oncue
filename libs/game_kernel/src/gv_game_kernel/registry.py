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
    EndRule,
    PartialRule,
    Scorer,
    Source,
)
from gv_game_kernel.spec import ModeConfig, error_path
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
        supported_sessions: Виды сессий, которые оркестрирует сервис (game — ``solo``).
        normalizer: Нормализатор ответов для собранных движков (по умолчанию —
            ``engine.answer_normalizer()``).
    """

    def __init__(
        self,
        registry: PluginRegistry,
        *,
        supported_sessions: frozenset[str] = frozenset({"solo"}),
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
        end_spec = config.session.end
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

        if clue is not None and clue_params is not None:
            levels_count = len(clue.levels(clue_params))
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
        end_rule = r.end_rule(config.session.end.type if config.session.end else DEFAULT_END_RULE)
        reveal = r.clue(config.clue.reveal.type) if config.clue.reveal else None
        partials = tuple(r.partial(kind) for kind in config.answer.partial)
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
    if config.session.attempts_per_level != 1:
        issues.append(ConfigIssue("session.attempts_per_level", SINGLE_ANSWER_LEVELS))
    return issues


def _parse(
    plugin: ClueType | Source | AnswerMatcher | Scorer | EndRule | None,
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
    "ANSWER_FORMAT_UNSUPPORTED",
    "COMPARISON_REQUIRED",
    "DEFAULT_END_RULE",
    "DOMAIN_UNSUPPORTED",
    "DUPLICATE_PARTIAL",
    "END_RULE_REQUIRED",
    "PARTIAL_DOMAIN_UNSUPPORTED",
    "PARTIAL_UNSUPPORTED",
    "RECORDS_FEATURE",
    "RECORDS_UNSUPPORTED",
    "SESSION_UNSUPPORTED",
    "SINGLE_ANSWER_LEVELS",
    "UNKNOWN_PLUGIN",
    "ConfigIssue",
    "DuplicatePluginError",
    "EngineFactory",
    "ModeEngine",
    "PluginRegistry",
]
