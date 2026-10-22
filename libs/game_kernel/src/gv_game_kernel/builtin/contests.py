"""Встроенные правила состязания онлайн-матча: ``race`` и ``parallel`` (ADR 0032, D3).

Общее для обоих правил (``_LevelContest``):

* раунд начинается с уровня 1 для всех игроков; у уровня — окно ``window_ms``
  (``session.level_windows_s``), время — аргумент ``now_ms`` (часы match);
* ход игрока применяется к его ``RoundState`` по ``round.apply_guess`` с правилами
  ``ContestRoundRules``: после ``attempts_per_level`` ответов игрок «ответил» и ждёт
  закрытия уровня (его ``RoundState`` уже на следующем уровне или завершён); пропуск
  (``Guess.kind = skip``) — пас на уровне; сдачи в раунде нет (сдача матча — у match);
* ход на закрытом уровне (окно истекло, досрочная блокировка) или после исчерпания
  попыток уровня — ``LevelLockedError``; в завершённом раунде — ``RoundFinishedError``;
* ``next_level`` открывает следующий уровень с новым окном или, если уровни кончились
  или все активные игроки завершили раунд, завершает раунд: незавершённые получают
  ``partial`` (если частичный ответ был засчитан) или ``miss``;
* вышедший игрок (``leave``) — ``void`` (если раунд для него ещё шёл), остальные играют.

Порядок ходов в одну миллисекунду — порядок вызовов: переходы чистые, match применяет
команды по порядку стрима комнаты.

Отличия правил — два флага, а не ветвление по ключу:

* ``race`` (дуэль): ``lock_on_first_correct`` — первый точный ответ закрывает уровень для
  всех (следующий уровень открывается сразу со своим окном); уровень закрывается и
  тогда, когда никто из активных игроков больше не может на нём ходить;
* ``parallel`` (party): уровень закрывается по окну или, при ``end_when_all_answered``,
  когда ответили все активные игроки.
"""

# ruff: noqa: ARG002 — методы реализуют протокол ContestRule, не всем нужны все аргументы

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Annotated, ClassVar

from pydantic import BaseModel, Field, StrictBool, StrictInt

from gv_game_kernel.builtin._base import PluginParams, typed
from gv_game_kernel.builtin.clues import CLASSIC_FORMAT, PICK_FORMAT
from gv_game_kernel.contest import ContestRoundState, PlayerRound, PlayerRoundView, PlayerStatus
from gv_game_kernel.errors import LevelLockedError, RoundFinishedError, RoundNotFinishedError
from gv_game_kernel.round import (
    Guess,
    GuessKind,
    MatchVerdict,
    RoundOutcome,
    RoundResult,
    RoundRules,
    RoundState,
    apply_guess,
    apply_skip,
    close_round,
    round_outcome,
    void,
)

AttemptsPerLevel = Annotated[StrictInt, Field(ge=1, le=5)]


@dataclass(frozen=True, slots=True)
class ContestRoundRules:
    """Правила ходов раунда игрока в матче (``RoundRules``).

    Attributes:
        attempts_per_level: Ответов на один уровень.
        skip_allowed: Пас на уровне (``POST .../match/skip``) — всегда можно.
        give_up_allowed: Сдаться в раунде нельзя: сдача — матча целиком (match).
    """

    attempts_per_level: int
    skip_allowed: bool = True
    give_up_allowed: bool = False


class RaceParams(PluginParams):
    """Параметры ``race`` (``contracts/modes/params/contest.race.v1.json``)."""

    attempts_per_level: AttemptsPerLevel
    lock_on_first_correct: StrictBool


class ParallelParams(PluginParams):
    """Параметры ``parallel`` (``contracts/modes/params/contest.parallel.v1.json``)."""

    attempts_per_level: AttemptsPerLevel
    end_when_all_answered: StrictBool


_MISSED = frozenset({RoundResult.MISS, RoundResult.GAVE_UP, RoundResult.VOID})


def _player(state: ContestRoundState, player_id: str) -> PlayerRound:
    try:
        return state.players[player_id]
    except KeyError:
        raise ValueError(f"игрок {player_id!r} не участвует в раунде") from None


def _can_act(state: ContestRoundState, player: PlayerRound) -> bool:
    """Игрок может ходить на открытом уровне: активен, раунд идёт, попытки уровня есть."""
    return player.active and not player.state.finished and player.state.level == state.level


def _active(state: ContestRoundState) -> list[PlayerRound]:
    return [p for p in state.players.values() if p.active]


class _LevelContest:
    """Общая механика уровней матча; правила отличаются флагами ``_locks``/``_closes_*``."""

    key: ClassVar[str]
    params_schema: ClassVar[type[BaseModel]]
    answer_formats: ClassVar[frozenset[str]]

    def _attempts(self, params: BaseModel) -> int:  # pragma: no cover — переопределяется
        raise NotImplementedError

    def _locks_on_exact(self, params: BaseModel) -> bool:
        """Точный ответ закрывает уровень для всех."""
        return False

    def _closes_when_all_answered(self, params: BaseModel) -> bool:
        """Закрыть уровень досрочно, когда никто из активных не может на нём ходить."""
        return True

    # ------------------------------------------------------------------------- протокол
    def round_rules(self, params: BaseModel) -> RoundRules:
        """Попытки на уровень из параметров; пас можно, сдаваться в раунде нельзя."""
        return ContestRoundRules(attempts_per_level=self._attempts(params))

    def start_round(
        self, player_ids: Sequence[str], *, now_ms: int, window_ms: int, params: BaseModel
    ) -> ContestRoundState:
        """Открыть уровень 1 для всех игроков (порядок игроков сохраняется).

        Raises:
            ValueError: нет игроков, повтор игрока или неположительное окно.
        """
        if not player_ids:
            raise ValueError("в раунде должен быть хотя бы один игрок")
        if len(set(player_ids)) != len(player_ids):
            raise ValueError("игроки раунда повторяются")
        if window_ms <= 0:
            raise ValueError("окно уровня должно быть положительным")
        return ContestRoundState(
            level=1,
            level_opened_at_ms=now_ms,
            level_ends_at_ms=now_ms + window_ms,
            players={pid: PlayerRound() for pid in player_ids},
        )

    def apply_guess(
        self,
        state: ContestRoundState,
        player_id: str,
        guess: Guess,
        *,
        levels_count: int,
        now_ms: int,
        params: BaseModel,
    ) -> ContestRoundState:
        """Применить сверенный ход (или пас) игрока на открытом уровне.

        Raises:
            RoundFinishedError: раунд завершён (для всех или для игрока, включая вышедшего).
            LevelLockedError: уровень закрыт (окно, блокировка) или попытки уровня исчерпаны.
            ValueError: неизвестный игрок или ход не на открытом уровне (ошибка вызова).
        """
        if state.finished:
            raise RoundFinishedError("раунд уже завершён")
        if state.level < 1:
            raise LevelLockedError("раунд ещё не начат", details={"level": state.level})
        player = _player(state, player_id)
        if player.state.finished or not player.active:
            raise RoundFinishedError(
                "раунд игрока завершён", details={"outcome": str(player.state.outcome)}
            )
        if self.level_closed(state, now_ms=now_ms, params=params):
            raise LevelLockedError("уровень закрыт", details={"level": state.level})
        if player.state.level != state.level:
            raise LevelLockedError("попытки уровня исчерпаны", details={"level": state.level})
        if guess.level != state.level:
            raise ValueError(f"ход на уровне {guess.level}, а открыт {state.level}")
        rules = self.round_rules(params)
        if guess.kind is GuessKind.SKIP:
            after = apply_skip(player.state, levels_count=levels_count, session=rules)
        else:
            after = apply_guess(player.state, guess, levels_count=levels_count, session=rules)
        updated = replace(player, state=after)
        scored_now = guess.verdict is MatchVerdict.EXACT or (
            player.state.partial_awarded_level is None and after.partial_awarded_level is not None
        )
        if scored_now:
            opened = state.level_opened_at_ms if state.level_opened_at_ms is not None else now_ms
            ends = state.level_ends_at_ms if state.level_ends_at_ms is not None else now_ms
            updated = replace(
                updated,
                answered_at_ms=now_ms,
                elapsed_ms=max(0, now_ms - opened),
                window_ms=max(1, ends - opened),
            )
        locked = state.level_locked or (
            guess.verdict is MatchVerdict.EXACT and self._locks_on_exact(params)
        )
        return replace(state, players={**state.players, player_id: updated}, level_locked=locked)

    def level_closed(self, state: ContestRoundState, *, now_ms: int, params: BaseModel) -> bool:
        """Уровень закрыт: раунд завершён или не начат, блокировка, окно истекло, никого
        из активных не осталось или (по правилу) никто больше не может ходить."""
        if state.finished or state.level < 1 or state.level_locked:
            return True
        if state.level_ends_at_ms is not None and now_ms >= state.level_ends_at_ms:
            return True
        active = _active(state)
        if not active:
            return True
        nobody_can_act = not any(_can_act(state, p) for p in active)
        return nobody_can_act and self._closes_when_all_answered(params)

    def next_level(
        self,
        state: ContestRoundState,
        *,
        levels_count: int,
        now_ms: int,
        window_ms: int,
        params: BaseModel,
    ) -> ContestRoundState:
        """Открыть следующий уровень с окном ``window_ms`` или завершить раунд (уровни
        кончились или все активные игроки завершили раунд).

        Raises:
            RoundFinishedError: раунд уже завершён.
            ValueError: уровень ещё открыт или окно неположительное (ошибка вызова).
        """
        if state.finished:
            raise RoundFinishedError("раунд уже завершён")
        if not self.level_closed(state, now_ms=now_ms, params=params):
            raise ValueError("уровень ещё открыт: next_level — после level_closed")
        everyone_done = all(p.state.finished for p in _active(state))
        if state.level >= levels_count or everyone_done:
            return replace(state, finished=True, players=_close_all(state.players))
        if window_ms <= 0:
            raise ValueError("окно уровня должно быть положительным")
        level = state.level + 1
        players = {
            pid: p
            if not p.active or p.state.finished or p.state.level >= level
            else replace(p, state=replace(p.state, level=level, attempts_on_level=0))
            for pid, p in state.players.items()
        }
        return replace(
            state,
            level=level,
            level_opened_at_ms=now_ms,
            level_ends_at_ms=now_ms + window_ms,
            level_locked=False,
            players=players,
        )

    def round_finished(self, state: ContestRoundState, *, params: BaseModel) -> bool:
        """Раунд завершён для всех (``next_level`` закрыл последний уровень)."""
        return state.finished

    def leave(self, state: ContestRoundState, player_id: str) -> ContestRoundState:
        """Игрок вышел: идущий раунд игрока — ``void``, завершённый не меняется.

        Raises:
            ValueError: неизвестный игрок.
        """
        player = _player(state, player_id)
        after = player.state if player.state.finished else void(player.state)
        return replace(
            state, players={**state.players, player_id: replace(player, state=after, active=False)}
        )

    def outcome(
        self, state: ContestRoundState, player_id: str, *, params: BaseModel
    ) -> RoundOutcome:
        """Итог раунда игрока: ``elapsed_ms``/``window_ms`` — время оцениваемого ответа.

        Raises:
            RoundNotFinishedError: раунд игрока ещё идёт.
            ValueError: неизвестный игрок.
        """
        player = _player(state, player_id)
        if not player.state.finished:
            raise RoundNotFinishedError("раунд игрока ещё идёт")
        return round_outcome(player.state, elapsed_ms=player.elapsed_ms, window_ms=player.window_ms)

    def views(self, state: ContestRoundState) -> list[PlayerRoundView]:
        """Статусы игроков в порядке старта раунда, без текстов ответов.

        Уровень угадывания виден, только когда уровень уже закрыт для всех (раунд
        завершён или открыт следующий уровень): до этого соперник не узнаёт, на каком
        уровне ответ.
        """
        return [_view(state, pid, p) for pid, p in state.players.items()]


def _close_all(players: Mapping[str, PlayerRound]) -> dict[str, PlayerRound]:
    return {
        pid: p if p.state.finished else replace(p, state=close_round(p.state))
        for pid, p in players.items()
    }


def _scored_level(round_state: RoundState) -> int | None:
    if round_state.outcome is RoundResult.EXACT:
        return round_state.level
    return round_state.partial_awarded_level


def _view(state: ContestRoundState, player_id: str, player: PlayerRound) -> PlayerRoundView:
    rs = player.state
    if rs.outcome is None:
        status = PlayerStatus.THINKING if _can_act(state, player) else PlayerStatus.ANSWERED
        return PlayerRoundView(player_id=player_id, status=status)
    if rs.outcome in _MISSED:
        return PlayerRoundView(player_id=player_id, status=PlayerStatus.MISSED)
    level = _scored_level(rs)
    shown = level if level is not None and (state.finished or level < state.level) else None
    return PlayerRoundView(player_id=player_id, status=PlayerStatus.GUESSED, level=shown)


class RaceContest(_LevelContest):
    """Дуэль: первый точный ответ (``lock_on_first_correct``) закрывает уровень для всех;
    формат — только ``classic`` (с вариантами ``pick`` опережение теряет смысл)."""

    key: ClassVar[str] = "race"
    params_schema: ClassVar[type[BaseModel]] = RaceParams
    answer_formats: ClassVar[frozenset[str]] = frozenset({CLASSIC_FORMAT})

    def _attempts(self, params: BaseModel) -> int:
        return typed(params, RaceParams).attempts_per_level

    def _locks_on_exact(self, params: BaseModel) -> bool:
        return typed(params, RaceParams).lock_on_first_correct


class ParallelContest(_LevelContest):
    """Party: все отвечают в окне уровня; ``end_when_all_answered`` закрывает уровень,
    когда ответили все активные игроки."""

    key: ClassVar[str] = "parallel"
    params_schema: ClassVar[type[BaseModel]] = ParallelParams
    answer_formats: ClassVar[frozenset[str]] = frozenset({CLASSIC_FORMAT, PICK_FORMAT})

    def _attempts(self, params: BaseModel) -> int:
        return typed(params, ParallelParams).attempts_per_level

    def _closes_when_all_answered(self, params: BaseModel) -> bool:
        return typed(params, ParallelParams).end_when_all_answered


__all__ = [
    "ContestRoundRules",
    "ParallelContest",
    "ParallelParams",
    "RaceContest",
    "RaceParams",
]
