"""Реестр плагинов и сборка движка режима из ``ModeConfig``.

``PluginRegistry`` наполняется при старте процесса (``register_builtin`` потока A + плагины
сервиса); повторная регистрация ключа — ошибка программиста, а не тихая подмена.
``EngineFactory.validate`` собирает ВСЕ проблемы конфига с JSON-путями — их показывает
редактор в админке; ``build`` отказывается собирать движок из невалидного конфига.

Шаг 0a: реестр готов полностью; ``validate`` проверяет только наличие ключей в реестре,
остальные правила (§4 плана, «EngineFactory.validate») и ``ModeEngine`` — поток A.
"""

from __future__ import annotations

from dataclasses import dataclass

from gv_game_kernel.errors import ConfigError
from gv_game_kernel.plugins import (
    AnswerInput,
    AnswerMatcher,
    ClueType,
    DrawContext,
    LevelSpec,
    MatchResult,
    PartialRule,
    Scorer,
    Source,
)
from gv_game_kernel.refs import MediaItemRef
from gv_game_kernel.round import RoundOutcome
from gv_game_kernel.spec import ModeConfig

UNKNOWN_PLUGIN = "config.unknown_plugin"
"""Код проблемы: ключа плагина нет в реестре."""


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


class ModeEngine:
    """Движок одного режима — фасад для game (и match в этапе 4).

    Собирается только ``EngineFactory.build`` из валидного конфига. Методы — сигнатуры
    шага 0a, реализация — поток A.
    """

    def __init__(
        self,
        config: ModeConfig,
        *,
        clue: ClueType,
        source: Source,
        matcher: AnswerMatcher,
        partials: tuple[PartialRule, ...],
        scorer: Scorer,
    ) -> None:
        self.config = config
        self.clue = clue
        self.source = source
        self.matcher = matcher
        self.partials = partials
        self.scorer = scorer

    def levels(self) -> list[LevelSpec]:
        """Уровни загадки режима."""
        raise NotImplementedError("TODO(stage2/A): ModeEngine.levels")

    async def draw(self, ctx: DrawContext, n: int) -> list[MediaItemRef]:
        """Разыграть ``n`` единиц через Source режима."""
        raise NotImplementedError("TODO(stage2/A): ModeEngine.draw")

    def check(self, answer: AnswerInput, item: MediaItemRef) -> MatchResult:
        """Сверить ответ (matcher + правила частичного ответа)."""
        raise NotImplementedError("TODO(stage2/A): ModeEngine.check")

    def score(self, outcome: RoundOutcome) -> int:
        """Очки за раунд."""
        raise NotImplementedError("TODO(stage2/A): ModeEngine.score")

    def max_score(self, rounds: int) -> int:
        """Максимум очков за ``rounds`` не-``void`` раундов."""
        raise NotImplementedError("TODO(stage2/A): ModeEngine.max_score")


class EngineFactory:
    """Проверка конфигов и сборка ``ModeEngine``.

    Args:
        registry: Реестр плагинов процесса.
        supported_sessions: Виды сессий, которые оркестрирует сервис (game — ``solo``).
    """

    def __init__(
        self, registry: PluginRegistry, *, supported_sessions: frozenset[str] = frozenset({"solo"})
    ) -> None:
        self._registry = registry
        self._supported_sessions = supported_sessions

    def validate(self, config: ModeConfig) -> list[ConfigIssue]:
        """Все проблемы конфига (пустой список — конфиг валиден).

        Шаг 0a проверяет наличие ключей плагинов в реестре.
        TODO(stage2/A): params по ``params_schema``, ``domain ∈ clue.domains``,
        ``answer.format``, ``session.kind``, ``len(scoring.points) == len(levels)``,
        применимость ``partial`` к домену (§4 плана).
        """
        r = self._registry
        issues = [
            ConfigIssue(path, UNKNOWN_PLUGIN)
            for path, found in (
                ("source.type", r.source(config.source.type)),
                ("clue.type", r.clue(config.clue.type)),
                ("answer.matcher.type", r.matcher(config.answer.matcher.type)),
                ("scoring.type", r.scorer(config.scoring.type)),
            )
            if found is None
        ]
        issues.extend(
            ConfigIssue(f"answer.partial[{i}]", UNKNOWN_PLUGIN)
            for i, kind in enumerate(config.answer.partial)
            if r.partial(kind) is None
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
        partials = tuple(r.partial(kind) for kind in config.answer.partial)
        if clue is None or source is None or matcher is None or scorer is None:
            raise AssertionError("validate пропустил неизвестный плагин")  # pragma: no cover
        return ModeEngine(
            config,
            clue=clue,
            source=source,
            matcher=matcher,
            partials=tuple(p for p in partials if p is not None),
            scorer=scorer,
        )
