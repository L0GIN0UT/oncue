"""Правила матча из конфига режима — ядро ``gv_game_kernel`` (ADR 0032, D2–D4).

match не ветвится по виду матча: всё, что отличает дуэль от party, — плагины конфига
(``ContestRule`` в ``session.contest``, ``Scorer`` в ``scoring``). ``RulesFactory`` разбирает
тело конфига (из плана game или internal-ручки конфига), применяет переопределения хоста
кастомного лобби и собирает ``KernelMatchRules`` — реализацию ``MatchRules`` автомата.

Плагины ``ContestRule`` подключает ядро (поток A этапа 4a); фабрика получает их функцией
поиска ``contest_of(key)``, места — функцией ``ranker`` (по умолчанию ``MatchOutcome.rank``).
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from gv_game_kernel import (
    AnswerInput,
    AssetPort,
    AssetRef,
    ClueView,
    ConfigError,
    ContestRoundState,
    ContestRule,
    EngineFactory,
    Guess,
    MatchOutcome,
    MatchResult,
    MediaItemRef,
    ModeConfig,
    ModeEngine,
    OnlineSessionSpec,
    OptionSpec,
    PersonRef,
    PlayerRoundView,
    PluginRegistry,
    RoundDraw,
    RoundOutcome,
    parse_mode_config,
)
from gv_game_kernel.outcome import Placement
from match_service.domain import errors
from match_service.domain.errors import MatchInvalidError
from match_service.domain.model import (
    ChatRules,
    ModeInfo,
    PlannedRound,
    RoomSettings,
    RoomTemplate,
    SettingsOptions,
)

ONLINE_SESSIONS = frozenset({"duel", "party", "lobby"})
MS = 1000

type ContestLookup = Callable[[str], ContestRule | None]
type Ranker = Callable[[Mapping[str, int], Collection[str]], list[Placement]]


def default_ranker(scores: Mapping[str, int], left: Collection[str]) -> list[Placement]:
    """Места по ``MatchOutcome.rank`` ядра."""
    return MatchOutcome.rank(scores, left=left)


@dataclass(frozen=True, slots=True)
class ModeTemplate:
    """Шаблон режима для создания комнаты."""

    mode: ModeInfo
    template: RoomTemplate


def _item_ref(round_: PlannedRound) -> MediaItemRef:
    key = round_.answer
    return MediaItemRef(
        id=key.media_item_id,
        domain=key.domain,
        kind=key.kind,
        title=key.title,
        year=key.year,
        aliases=key.aliases,
        title_aliases=key.title_aliases,
        persons=tuple(PersonRef(id=p.person_id, name=p.name, role=p.role) for p in key.persons),
    )


def _draw(round_: PlannedRound) -> RoundDraw:
    return RoundDraw(
        target=_item_ref(round_),
        options=tuple(
            OptionSpec(
                id=o.option_id,
                label_key=o.label_key,
                item=MediaItemRef(id=o.media_item_id, domain="", kind="", title=o.title or "")
                if o.media_item_id is not None
                else None,
            )
            for o in round_.options
        ),
    )


class KernelMatchRules:
    """``MatchRules`` поверх ``ModeEngine`` и ``ContestRule`` (реализация автомата).

    Args:
        engine: Движок режима (загадка, matcher, scorer).
        contest: Плагин состязания.
        contest_params: Разобранные параметры ``session.contest``.
        session: Онлайн-сессия конфига.
        ranker: Места матча.
    """

    def __init__(
        self,
        engine: ModeEngine,
        contest: ContestRule,
        contest_params: BaseModel,
        session: OnlineSessionSpec,
        ranker: Ranker,
    ) -> None:
        self.engine = engine
        self.contest = contest
        self.params = contest_params
        self.session = session
        self._ranker = ranker
        self._rules = contest.round_rules(contest_params)

    # ------------------------------------------------------------------ параметры
    @property
    def levels_count(self) -> int:
        """Уровней загадки."""
        return self.engine.levels_count

    @property
    def countdown_ms(self) -> int:
        """Отсчёт."""
        return self.session.countdown_s * MS

    @property
    def reveal_ms(self) -> int:
        """Ревил."""
        return self.session.reveal_s * MS

    @property
    def grace_ms(self) -> int:
        """Ожидание отключившегося."""
        return self.session.disconnect_grace_s * MS

    @property
    def attempts_per_level(self) -> int:
        """Ответов на уровень."""
        return self._rules.attempts_per_level

    def window_ms(self, level: int) -> int:
        """Окно уровня (с 1); за пределами списка — последнее окно."""
        windows = self.session.level_windows_s
        return windows[min(level, len(windows)) - 1] * MS

    # ------------------------------------------------------------------ раунд
    def start_round(self, player_ids: Sequence[str], now_ms: int) -> ContestRoundState:
        """Уровень 1 для всех."""
        return self.contest.start_round(
            player_ids, now_ms=now_ms, window_ms=self.window_ms(1), params=self.params
        )

    def apply_guess(
        self, state: ContestRoundState, player_id: str, guess: Guess, now_ms: int
    ) -> ContestRoundState:
        """Сверенный ход."""
        return self.contest.apply_guess(
            state,
            player_id,
            guess,
            levels_count=self.levels_count,
            now_ms=now_ms,
            params=self.params,
        )

    def level_closed(self, state: ContestRoundState, now_ms: int) -> bool:
        """Уровень закрыт."""
        return self.contest.level_closed(state, now_ms=now_ms, params=self.params)

    def next_level(self, state: ContestRoundState, now_ms: int) -> ContestRoundState:
        """Следующий уровень."""
        return self.contest.next_level(
            state,
            levels_count=self.levels_count,
            now_ms=now_ms,
            window_ms=self.window_ms(state.level + 1),
            params=self.params,
        )

    def round_finished(self, state: ContestRoundState) -> bool:
        """Раунд окончен."""
        return self.contest.round_finished(state, params=self.params)

    def leave(self, state: ContestRoundState, player_id: str) -> ContestRoundState:
        """Выход игрока."""
        return self.contest.leave(state, player_id)

    def outcome(self, state: ContestRoundState, player_id: str) -> RoundOutcome:
        """Итог раунда игрока."""
        return self.contest.outcome(state, player_id, params=self.params)

    def score(self, outcome: RoundOutcome) -> int:
        """Очки."""
        return self.engine.score(outcome)

    def views(self, state: ContestRoundState) -> list[PlayerRoundView]:
        """Статусы игроков."""
        return self.contest.views(state)

    def rank(self, scores: Mapping[str, int], left: Collection[str]) -> list[Placement]:
        """Места."""
        return self._ranker(scores, left)

    # ------------------------------------------------------------------ ответ и ассеты
    def check(self, answer: AnswerInput, round_: PlannedRound) -> MatchResult:
        """Сверить ответ с ключом раунда (``AnswerKindError``/``OptionNotFoundError`` ядра)."""
        draw = _draw(round_)
        return self.engine.check(answer, draw.target, draw if round_.options else None)

    async def clue(
        self, round_: PlannedRound, level: int, *, assets: AssetPort, subject: str
    ) -> ClueView | None:
        """Подача уровня (``None`` — набора уровня нет в плане)."""
        asset_id = round_.asset_for(level)
        if asset_id is None:
            return None
        asset = AssetRef(asset_set_id=asset_id, levels_count=self.levels_count)
        return await self.engine.clue.reveal(
            asset, level, self.engine.clue_params, assets=assets, subject=subject
        )

    async def final_clue(
        self, round_: PlannedRound, *, assets: AssetPort, subject: str
    ) -> ClueView | None:
        """Подача ревила (последний уровень или загадка ревила, ADR 0026)."""
        last = round_.asset_for(self.levels_count)
        if last is None:
            return None
        reveal = (
            AssetRef(asset_set_id=round_.reveal_asset_set_id, levels_count=1)
            if round_.reveal_asset_set_id is not None
            else None
        )
        return await self.engine.final_view(
            AssetRef(asset_set_id=last, levels_count=self.levels_count),
            assets=assets,
            subject=subject,
            reveal_asset=reveal,
        )


def _online(config: ModeConfig) -> OnlineSessionSpec:
    session = config.session
    if not isinstance(session, OnlineSessionSpec):
        raise MatchInvalidError(
            "режим не онлайн", code=errors.MODE_NOT_ONLINE, details={"kind": session.kind}
        )
    return session


def effective_body(
    body: Mapping[str, Any], settings: RoomSettings, template: RoomTemplate
) -> dict[str, Any]:  # Any: тело ModeConfig
    """Тело конфига с переопределениями хоста (D12): окна, формат, загадка, места.

    Загадка выбирается из ``session.lobby.overridable.clue`` по типу. Число раундов в тело
    не пишется: его задаёт план (``rounds`` запроса), а параметры источника (например,
    ``round_options`` сборника) проверяются по шаблону.
    """
    result: dict[str, Any] = copy.deepcopy(dict(body))  # Any: тело ModeConfig
    session = result["session"]
    session["players"] = {**session["players"], "max": settings.players_max}
    if settings.answer_format != template.answer_format:
        result["answer"]["format"] = settings.answer_format
    if settings.clue_type != template.clue_type:
        options = ((session.get("lobby") or {}).get("overridable") or {}).get("clue") or []
        chosen = next((c for c in options if c.get("type") == settings.clue_type), None)
        if chosen is None:
            raise MatchInvalidError(
                "загадка недоступна", code=errors.SETTING_NOT_ALLOWED, details={"field": "clue"}
            )
        result["clue"] = copy.deepcopy(chosen)
    if settings.level_window_s is not None:
        session["level_windows_s"] = [settings.level_window_s] * len(session["level_windows_s"])
    return result


class RulesFactory:
    """Разбор конфигов онлайн-режимов и сборка правил (кэш по телу не нужен: дёшево).

    Args:
        registry: Реестр плагинов ядра.
        contest_of: Поиск ``ContestRule`` по ключу.
        ranker: Места матча.
    """

    def __init__(
        self, registry: PluginRegistry, contest_of: ContestLookup, ranker: Ranker = default_ranker
    ) -> None:
        self._factory = EngineFactory(registry, supported_sessions=ONLINE_SESSIONS)
        self._contest_of = contest_of
        self._ranker = ranker

    def _engine(self, config: ModeConfig) -> tuple[ModeEngine, ContestRule, BaseModel]:
        session = _online(config)
        contest = self._contest_of(session.contest.type)
        if contest is None:
            raise ConfigError(
                details={"errors": [{"path": "session.contest.type", "code": "config.unknown"}]}
            )
        params = contest.params_schema.model_validate(session.contest.params)
        return self._factory.build(config), contest, params

    def template(self, key: str, version: int, body: Mapping[str, Any]) -> ModeTemplate:
        """Шаблон комнаты из опубликованного конфига.

        Raises:
            MatchInvalidError: ``match.mode_not_online``.
            ConfigError: конфиг не собирается (плагин неизвестен).
        """
        config = parse_mode_config(body)
        session = _online(config)
        engine, _, _ = self._engine(config)
        lobby = session.lobby
        options = None
        if lobby is not None:
            o = lobby.overridable
            options = SettingsOptions(
                rounds=o.rounds,
                level_window_s=o.level_window_s,
                answer_format=o.answer_format,
                clue_type=tuple(c.type for c in o.clue) if o.clue is not None else None,
                players_max=o.players_max,
                visibility=o.visibility,
            )
        uses_collection = engine.uses_collection
        max_collections = int(getattr(engine.source_params, "max_collections", 1) or 1)
        return ModeTemplate(
            mode=ModeInfo(
                key=key,
                version=version,
                domain=config.domain,
                kind=session.kind,
                title_key=config.title_i18n,
                ranked=session.ranked,
                ladder=session.ladder,
            ),
            template=RoomTemplate(
                players_min=session.players.min,
                players_max=session.players.max,
                rounds=session.rounds,
                level_windows_s=tuple(session.level_windows_s),
                answer_format=config.answer.format,
                clue_type=config.clue.type,
                visibility=lobby.visibility if lobby is not None else "private",
                options=options,
                chat=ChatRules(
                    emotes=tuple(session.chat.emotes),
                    phrases=tuple(session.chat.phrases),
                    free_text=session.chat.free_text,
                ),
                uses_collection=uses_collection,
                max_collections=max_collections if uses_collection else 0,
            ),
        )

    def build(self, body: Mapping[str, Any]) -> KernelMatchRules:
        """Правила матча по телу конфига (уже с переопределениями).

        Raises:
            ConfigError: конфиг не собирается.
        """
        config = parse_mode_config(body)
        engine, contest, params = self._engine(config)
        return KernelMatchRules(engine, contest, params, _online(config), self._ranker)


def media_ids(plan: Sequence[PlannedRound]) -> list[UUID]:
    """Единицы плана (память лобби)."""
    return [r.answer.media_item_id for r in plan]
