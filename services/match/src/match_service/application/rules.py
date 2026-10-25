"""Правила матча из конфига режима — ядро ``gv_game_kernel`` (ADR 0032, D2–D4).

match не ветвится по виду матча: всё, что отличает дуэль от party, — плагины конфига
(``ContestRule`` в ``session.contest``, ``Scorer`` в ``scoring``). ``RulesFactory`` разбирает
тело конфига (из плана game или internal-ручки конфига), применяет переопределения хоста
кастомного лобби и собирает ``KernelMatchRules`` — реализацию ``MatchRules`` автомата.

Раунд ведёт только фасад ядра ``ModeEngine.contest_*``, места — ``MatchOutcome.rank``.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from gv_game_kernel import (
    ONLINE_SESSIONS,
    AnswerInput,
    AssetPort,
    AssetRef,
    ClueView,
    ContestRoundState,
    ContestStep,
    EngineFactory,
    MatchOutcome,
    MediaItemRef,
    ModeConfig,
    ModeEngine,
    OnlineSessionSpec,
    OptionSpec,
    OverrideNotAllowedError,
    PersonRef,
    PlayerRoundView,
    PluginRegistry,
    RoundDraw,
    RoundOutcome,
    apply_lobby_overrides,
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

MS = 1000


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
    """``MatchRules`` поверх фасада ``ModeEngine.contest_*`` (реализация автомата).

    Args:
        engine: Движок онлайн-режима (загадка, matcher, ``ContestRule``, scorer).
    """

    def __init__(self, engine: ModeEngine) -> None:
        self.engine = engine
        self.session = engine.online_session
        self._attempts = engine.round_rules.attempts_per_level

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
        return self._attempts

    # ------------------------------------------------------------------ раунд
    def start_round(self, player_ids: Sequence[str], now_ms: int) -> ContestRoundState:
        """Уровень 1 для всех."""
        return self.engine.contest_start(player_ids, now_ms=now_ms)

    def guess(
        self,
        state: ContestRoundState,
        player_id: str,
        answer: AnswerInput,
        round_: PlannedRound,
        *,
        display: str,
        now_ms: int,
    ) -> ContestStep:
        """Сверить ответ с ключом раунда и применить ход (ошибки — как у ядра)."""
        return self.engine.contest_guess(
            state, player_id, answer, _draw(round_), display=display, now_ms=now_ms
        )

    def skip(self, state: ContestRoundState, player_id: str, now_ms: int) -> ContestRoundState:
        """Пас."""
        return self.engine.contest_skip(state, player_id, now_ms=now_ms)

    def level_closed(self, state: ContestRoundState, now_ms: int) -> bool:
        """Уровень закрыт."""
        return self.engine.contest_level_closed(state, now_ms=now_ms)

    def next_level(self, state: ContestRoundState, now_ms: int) -> ContestRoundState:
        """Следующий уровень или конец раунда."""
        return self.engine.contest_advance(state, now_ms=now_ms)

    def round_finished(self, state: ContestRoundState) -> bool:
        """Раунд окончен."""
        return self.engine.contest_round_finished(state)

    def leave(self, state: ContestRoundState, player_id: str) -> ContestRoundState:
        """Выход игрока."""
        return self.engine.contest_leave(state, player_id)

    def outcome(self, state: ContestRoundState, player_id: str) -> RoundOutcome:
        """Итог раунда игрока."""
        return self.engine.contest_outcome(state, player_id)

    def score(self, outcome: RoundOutcome) -> int:
        """Очки."""
        return self.engine.score(outcome)

    def views(self, state: ContestRoundState) -> list[PlayerRoundView]:
        """Статусы игроков."""
        return self.engine.contest_views(state)

    def rank(self, scores: Mapping[str, int], left: Collection[str]) -> list[Placement]:
        """Места (``MatchOutcome.rank``)."""
        return MatchOutcome.rank(scores, left=left)

    # ------------------------------------------------------------------ ассеты
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
    try:
        # Загадка и формат — общий помощник ядра: то же правило допустимости, что в game.
        result = apply_lobby_overrides(
            body,
            clue_type=settings.clue_type if settings.clue_type != template.clue_type else None,
            answer_format=(
                settings.answer_format if settings.answer_format != template.answer_format else None
            ),
        )
    except OverrideNotAllowedError as exc:
        field = (
            "clue_type"
            if (exc.details or {}).get("field") == "clue"
            else (exc.details or {}).get("field")
        )
        raise MatchInvalidError(
            "значение настройки не разрешено шаблоном",
            code=errors.SETTING_NOT_ALLOWED,
            details={"field": field},
        ) from exc
    session = result["session"]
    session["players"] = {**session["players"], "max": settings.players_max}
    if settings.level_window_s is not None:
        session["level_windows_s"] = [settings.level_window_s] * len(session["level_windows_s"])
    return result


class RulesFactory:
    """Разбор конфигов онлайн-режимов и сборка правил.

    Args:
        registry: Реестр плагинов ядра (встроенные, включая ``ContestRule``).
    """

    def __init__(self, registry: PluginRegistry) -> None:
        self._factory = EngineFactory(registry, supported_sessions=ONLINE_SESSIONS)

    def _engine(self, config: ModeConfig) -> ModeEngine:
        _online(config)
        return self._factory.build(config)

    def template(self, key: str, version: int, body: Mapping[str, Any]) -> ModeTemplate:
        """Шаблон комнаты из опубликованного конфига.

        Raises:
            MatchInvalidError: ``match.mode_not_online``.
            ConfigError: конфиг не собирается (плагин неизвестен).
        """
        config = parse_mode_config(body)
        session = _online(config)
        engine = self._engine(config)
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
        return KernelMatchRules(self._engine(parse_mode_config(body)))


def media_ids(plan: Sequence[PlannedRound]) -> list[UUID]:
    """Единицы плана (память лобби)."""
    return [r.answer.media_item_id for r in plan]
