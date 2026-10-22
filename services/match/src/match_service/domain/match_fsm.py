"""Автомат матча (R2 плана этапа 4, D3, D10).

``lobby → countdown → level_open → (level_closed) → … → reveal → … → summary → closed``;
``paused`` — матч на двоих при отключении игрока. Исходы: ``completed``, ``forfeit``,
``tech_win``, ``aborted``.

Все переходы — по командам и дедлайнам ``phase_ends_at_ms``; время — аргумент ``now_ms``
(часы у application). Правила раунда (открытие и закрытие уровней, попытки, очки, места)
даёт ядро через ``MatchRules`` — автомат не ветвится по виду матча: «дуэль» и «party»
различаются плагином ``ContestRule`` (D3). Единственное правило по составу — пауза при
отключении в матче на двоих (D10): без соперника такой матч продолжать бессмысленно.

``level_closed`` в 4a мгновенна: по закрытию уровня в канал уходит ``level_closed``, затем
сразу открывается следующий уровень или ревил (окна уровней — из конфига режима, отдельной
паузы между уровнями конфиг не задаёт).

Ссылку уровня автомат не получает сам: переход возвращает ``NeedClue``, application
запрашивает ссылку (D11) и вызывает ``set_clue`` — ``level_opened`` уходит уже со ссылкой.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID

from gv_game_kernel import (
    AnswerInput,
    ContestRoundState,
    ContestStep,
    PlayerRoundView,
    PlayerStatus,
    RoundOutcome,
    RoundResult,
)
from gv_game_kernel.outcome import Placement
from match_service.domain import errors
from match_service.domain.errors import MatchConflictError, MatchForbiddenError
from match_service.domain.model import (
    Clue,
    MatchFinish,
    MatchPlayer,
    MatchState,
    Paused,
    Phase,
    PlannedRound,
    PlayerFinish,
    PlayerRoundResult,
    Presence,
    RoundRecord,
    Standing,
)
from match_service.domain.room import Room

PAUSE_PLAYERS = 2
"""Матч на двоих при отключении игрока ставится на паузу (D10), больше — идёт дальше."""


class MatchRules(Protocol):
    """Правила матча из конфига режима (ядро: ``ContestRule``, ``Scorer``, ``MatchOutcome``).

    Реализация — application (``KernelMatchRules``); в unit-тестах — простые двойники.
    """

    @property
    def levels_count(self) -> int:
        """Уровней загадки."""
        ...

    @property
    def countdown_ms(self) -> int:
        """Отсчёт перед первым раундом."""
        ...

    @property
    def reveal_ms(self) -> int:
        """Длительность ревила."""
        ...

    @property
    def grace_ms(self) -> int:
        """``disconnect_grace_s``."""
        ...

    @property
    def attempts_per_level(self) -> int:
        """Ответов на уровень."""
        ...

    def start_round(self, player_ids: Sequence[str], now_ms: int) -> ContestRoundState:
        """Открыть уровень 1 раунда."""
        ...

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
        """Сверить и применить ход (``LevelLockedError`` ядра — уровень закрыт)."""
        ...

    def skip(self, state: ContestRoundState, player_id: str, now_ms: int) -> ContestRoundState:
        """Пас на открытом уровне."""
        ...

    def level_closed(self, state: ContestRoundState, now_ms: int) -> bool:
        """Закрыт ли текущий уровень."""
        ...

    def next_level(self, state: ContestRoundState, now_ms: int) -> ContestRoundState:
        """Открыть следующий уровень или завершить раунд."""
        ...

    def round_finished(self, state: ContestRoundState) -> bool:
        """Раунд окончен для всех."""
        ...

    def leave(self, state: ContestRoundState, player_id: str) -> ContestRoundState:
        """Игрок вышел из раунда."""
        ...

    def outcome(self, state: ContestRoundState, player_id: str) -> RoundOutcome:
        """Итог раунда игрока."""
        ...

    def score(self, outcome: RoundOutcome) -> int:
        """Очки за раунд."""
        ...

    def views(self, state: ContestRoundState) -> list[PlayerRoundView]:
        """Статусы игроков без текстов ответов."""
        ...

    def rank(self, scores: Mapping[str, int], left: Collection[str]) -> list[Placement]:
        """Места матча (``MatchOutcome.rank``)."""
        ...


@dataclass(frozen=True, slots=True)
class Timing:
    """Параметры сервиса (не режима): простой комнаты, присутствие, память лобби.

    Attributes:
        idle_ms: ``GV_MATCH__ROOM_IDLE_CLOSE_MIN`` — простой лобби и длительность итога.
        presence_timeout_ms: Без heartbeat дольше — «Отключён».
        memory_limit: Сколько единиц помнит лобби (``exclude_ids`` плана).
    """

    idle_ms: int
    presence_timeout_ms: int
    memory_limit: int


@dataclass(frozen=True, slots=True)
class NeedClue:
    """Нужна ссылка открытого уровня (D11)."""

    round_index: int
    level: int


@dataclass(frozen=True, slots=True)
class NeedFinalClue:
    """Нужен ассет ревила раунда."""

    round_index: int


type Need = NeedClue | NeedFinalClue


def _pid(user_id: UUID) -> str:
    return str(user_id)


def _contest(match: MatchState) -> ContestRoundState:
    if match.contest is None:
        raise MatchConflictError("раунд не идёт", code=errors.PHASE_MISMATCH)
    return match.contest


def _match(room: Room) -> MatchState:
    if room.match is None:
        raise MatchConflictError("в комнате нет матча", code=errors.PHASE_MISMATCH)
    return room.match


# ----------------------------------------------------------------------------- старт
def begin(room: Room, match: MatchState, rules: MatchRules, now_ms: int) -> None:
    """Матч получен (план есть): отсчёт ``countdown_s``."""
    room.match = match
    room.start_error = None
    room.rematch_votes.clear()
    room.rematch_code = None
    for seat in room.seats:
        seat.ready = False
    room.touch(now_ms)
    room.set_phase(
        Phase.COUNTDOWN,
        now_ms + rules.countdown_ms,
        round_index=None,
        level=None,
        rounds_total=match.rounds_total,
    )


# ----------------------------------------------------------------------------- ходы
def require_turn(
    room: Room,
    user_id: UUID,
    *,
    round_index: int,
    level: int,
    expected_version: int,
) -> MatchPlayer:
    """Ход игрока допустим: фаза уровня, тот же раунд и уровень, свежая версия, есть попытки.

    Raises:
        MatchForbiddenError: не участник матча или выбыл.
        MatchConflictError: ``match.phase_mismatch`` (другая фаза, раунд, уровень или
            ``expected_version`` из прошлой фазы), ``match.level_locked`` (попытки уровня
            кончились или раунд для игрока завершён).
    """
    room.require_phase(Phase.LEVEL_OPEN)
    match = _match(room)
    player = match.players.get(user_id)
    if player is None or not player.active:
        raise MatchForbiddenError("вы не участник матча", code=errors.NOT_PARTICIPANT)
    contest = match.contest
    if (
        contest is None
        or round_index != match.round_index
        or level != contest.level
        or expected_version < room.phase_version
    ):
        raise MatchConflictError(
            "команда не для текущего уровня",
            code=errors.PHASE_MISMATCH,
            details={"round_index": match.round_index, "level": contest.level if contest else None},
        )
    pr = contest.players.get(_pid(user_id))
    if pr is None or not pr.active or pr.state.finished:
        raise MatchConflictError("уровень для вас закрыт", code=errors.LEVEL_LOCKED)
    return player


def _status_of(rules: MatchRules, contest: ContestRoundState, pid: str) -> PlayerStatus | None:
    return next((v.status for v in rules.views(contest) if v.player_id == pid), None)


def commit(
    room: Room, rules: MatchRules, user_id: UUID, state: ContestRoundState, now_ms: int
) -> None:
    """Принять состояние раунда после хода игрока (ответ или пас)."""
    match = _match(room)
    pid = _pid(user_id)
    before = _status_of(rules, _contest(match), pid)
    match.contest = state
    room.touch(now_ms)
    if _status_of(rules, state, pid) != before:
        room.emit("player", user_id=pid)


def refresh_statuses(room: Room, rules: MatchRules) -> None:
    """Обновить статусы игроков (виды комнаты строятся без ядра)."""
    match = room.match
    if match is None:
        return
    statuses: dict[str, str] = {}
    for uid in match.players:
        status = player_status(room, rules, uid)
        if status is not None:
            statuses[_pid(uid)] = status.value
    match.statuses = statuses


def player_status(room: Room, rules: MatchRules, user_id: UUID) -> PlayerStatus | None:
    """Статус игрока в текущем раунде (``None`` вне фазы уровня)."""
    match = room.match
    if match is None or match.contest is None or room.phase is not Phase.LEVEL_OPEN:
        return None
    return _status_of(rules, match.contest, _pid(user_id))


# ----------------------------------------------------------------------------- таймеры
def advance(room: Room, rules: MatchRules | None, timing: Timing, now_ms: int) -> list[Need]:
    """Провести все переходы, которые наступили к ``now_ms`` (дедлайны и досрочные).

    ``rules`` — ``None``, если в комнате нет матча (лобби, итог).
    """
    needs: list[Need] = []
    levels = rules.levels_count if rules is not None else 1
    for _ in range(4 * max(1, levels) + 8):  # предохранитель от зацикливания
        step = _step(room, rules, timing, now_ms)
        if step is None:
            break
        needs.extend(step)
    return needs


def _expired(room: Room, now_ms: int) -> bool:
    return room.phase_ends_at_ms is not None and now_ms >= room.phase_ends_at_ms


def _step(room: Room, rules: MatchRules | None, timing: Timing, now_ms: int) -> list[Need] | None:
    """Один переход или ``None``, если ничего не наступило."""
    if room.phase is Phase.LOBBY:
        return _step_lobby(room, timing, now_ms)
    if room.phase is Phase.SUMMARY:
        return _step_summary(room, now_ms)
    handler = _STEPS.get(room.phase)
    if handler is None or rules is None:
        return None
    return handler(room, rules, timing, now_ms)


def _step_lobby(room: Room, timing: Timing, now_ms: int) -> list[Need] | None:
    if now_ms - room.activity_at_ms < timing.idle_ms:
        return None
    room.close("idle", now_ms)
    return []


def _step_summary(room: Room, now_ms: int) -> list[Need] | None:
    if not _expired(room, now_ms):
        return None
    room.close("finished", now_ms)
    return []


def _step_countdown(
    room: Room, rules: MatchRules, timing: Timing, now_ms: int
) -> list[Need] | None:
    del timing
    if room.match is None or not _expired(room, now_ms):
        return None
    room.match.started_at_ms = now_ms
    return _open_round(room, rules, now_ms)


def _step_level(room: Room, rules: MatchRules, timing: Timing, now_ms: int) -> list[Need] | None:
    del timing
    match = room.match
    if match is None or match.contest is None or match.clue_pending:
        return None
    if rules.level_closed(match.contest, now_ms) or _expired(room, now_ms):
        return _close_level(room, rules, now_ms)
    return None


def _step_reveal(room: Room, rules: MatchRules, timing: Timing, now_ms: int) -> list[Need] | None:
    match = room.match
    if match is None or not _expired(room, now_ms):
        return None
    if match.round_index + 1 < match.rounds_total and len(match.active_ids()) >= PAUSE_PLAYERS:
        match.round_index += 1
        return _open_round(room, rules, now_ms)
    _end(room, rules, timing, MatchFinish.COMPLETED, now_ms)
    return []


def _step_paused(room: Room, rules: MatchRules, timing: Timing, now_ms: int) -> list[Need] | None:
    if room.match is None or not _expired(room, now_ms):
        return None
    _tech_win(room, rules, timing, now_ms)
    return []


type _Step = Callable[[Room, MatchRules, Timing, int], list[Need] | None]
_STEPS: dict[Phase, _Step] = {
    Phase.COUNTDOWN: _step_countdown,
    Phase.LEVEL_OPEN: _step_level,
    Phase.REVEAL: _step_reveal,
    Phase.PAUSED: _step_paused,
}


def _open_round(room: Room, rules: MatchRules, now_ms: int) -> list[Need]:
    match = _match(room)
    ids = [_pid(u) for u in match.active_ids()]
    match.contest = rules.start_round(ids, now_ms)
    return _open_level(room, now_ms)


def _open_level(room: Room, now_ms: int) -> list[Need]:
    match = _match(room)
    contest = _contest(match)
    match.clue = None
    match.final_clue = None
    match.clue_pending = True
    room.touch(now_ms)
    room.set_phase(
        Phase.LEVEL_OPEN,
        contest.level_ends_at_ms,
        round_index=match.round_index,
        level=contest.level,
        rounds_total=match.rounds_total,
    )
    return [NeedClue(match.round_index, contest.level)]


def set_clue(
    room: Room,
    rules: MatchRules,
    *,
    round_index: int,
    level: int,
    clue: Clue | None,
    now_ms: int,
) -> list[Need]:
    """Ссылка открытого уровня получена.

    ``None`` — ассет недоступен (D11, решение оркестратора): на уровне 1 раунд ``void`` для
    всех; на следующих — угадавшие на прошлых уровнях сохраняют очки, раунд ``void`` только
    для неугадавших.
    """
    match = _match(room)
    contest = match.contest
    if (
        room.phase is not Phase.LEVEL_OPEN
        or contest is None
        or match.round_index != round_index
        or contest.level != level
    ):
        return []  # уровень уже сменился (например, пауза или выход) — ссылка не нужна
    match.clue_pending = False
    match.clue = clue
    room.emit(
        "level_opened",
        round_index=round_index,
        level=level,
        phase_ends_at_ms=room.phase_ends_at_ms,
        clue=clue,
    )
    if clue is None:
        if level == 1:
            return _finish_round(room, rules, now_ms, void=True)
        for pid, pr in contest.players.items():
            if pr.active and not pr.state.finished:
                contest = rules.leave(contest, pid)
        match.contest = contest
        return _finish_round(room, rules, now_ms, void=False)
    return []


def _close_level(room: Room, rules: MatchRules, now_ms: int) -> list[Need]:
    match = _match(room)
    contest = _contest(match)
    room.emit(
        "level_closed",
        round_index=match.round_index,
        level=contest.level,
        players=[
            {"user_id": v.player_id, "status": v.status.value, "level": v.level}
            for v in rules.views(contest)
        ],
    )
    if rules.round_finished(contest):
        return _finish_round(room, rules, now_ms, void=False)
    nxt = rules.next_level(contest, now_ms)
    match.contest = nxt
    if nxt.finished or rules.round_finished(nxt):
        return _finish_round(room, rules, now_ms, void=False)
    return _open_level(room, now_ms)


def _finish_round(room: Room, rules: MatchRules, now_ms: int, *, void: bool) -> list[Need]:
    match = _match(room)
    contest = _contest(match)
    plan = match.current
    results: list[PlayerRoundResult] = []
    for uid in sorted(match.players, key=lambda u: match.players[u].slot):
        pid = _pid(uid)
        pr = contest.players.get(pid)
        if pr is None:
            continue
        if void:
            outcome = RoundOutcome(result=RoundResult.VOID)
            points = 0
        else:
            outcome = rules.outcome(contest, pid)
            points = rules.score(outcome)
        player = match.players[uid]
        player.score += points
        results.append(
            PlayerRoundResult(
                user_id=uid,
                level=outcome.level,
                partial=outcome.result is RoundResult.PARTIAL,
                score=points,
                elapsed_ms=outcome.elapsed_ms if outcome.level or outcome.partial_level else None,
            )
        )
    match.records.append(
        RoundRecord(
            index=match.round_index,
            media_item_id=plan.answer.media_item_id,
            void=void,
            results=tuple(results),
        )
    )
    match.clue = None
    match.clue_pending = False
    room.touch(now_ms)
    room.set_phase(
        Phase.REVEAL,
        now_ms + rules.reveal_ms,
        round_index=match.round_index,
        level=None,
        rounds_total=match.rounds_total,
    )
    room.emit("round_reveal", round_index=match.round_index)
    return [NeedFinalClue(match.round_index)]


def set_final_clue(room: Room, round_index: int, clue: Clue | None) -> None:
    """Ассет ревила раунда получен (если ревил ещё идёт)."""
    match = room.match
    if match is None or room.phase is not Phase.REVEAL or match.round_index != round_index:
        return
    match.final_clue = clue


# ----------------------------------------------------------------------------- выход
def leave(room: Room, rules: MatchRules, timing: Timing, user_id: UUID, now_ms: int) -> None:
    """Игрок уходит из идущего матча: выбывает, очки сохраняются."""
    _drop(room, rules, timing, user_id, finish=PlayerFinish.LEFT, now_ms=now_ms)


def forfeit(room: Room, rules: MatchRules, timing: Timing, user_id: UUID, now_ms: int) -> None:
    """Сдаться: в матче на двоих — поражение (``forfeit``), иначе — выбывание из матча.

    Сдача — исход матча, а не выход из комнаты: место остаётся (присутствие как есть),
    сдавшийся видит итог и голосует за реванш; из комнаты уводят только ``leave``,
    выбывание по grace, кик и удаление аккаунта.
    """
    room.require_phase(Phase.COUNTDOWN, Phase.LEVEL_OPEN, Phase.REVEAL, Phase.PAUSED)
    _drop(
        room,
        rules,
        timing,
        user_id,
        finish=PlayerFinish.FORFEIT,
        now_ms=now_ms,
        leave_room=False,
    )


def _drop(
    room: Room,
    rules: MatchRules,
    timing: Timing,
    user_id: UUID,
    *,
    finish: PlayerFinish,
    now_ms: int,
    leave_room: bool = True,
) -> None:
    match = _match(room)
    player = match.players.get(user_id)
    if player is None or not player.active:
        raise MatchForbiddenError("вы не участник матча", code=errors.NOT_PARTICIPANT)
    two = len(match.active_ids()) == PAUSE_PLAYERS
    player.finish = finish
    if match.contest is not None:
        match.contest = rules.leave(match.contest, _pid(user_id))
    if leave_room:
        room.mark_left(user_id, now_ms)
    else:
        room.touch(now_ms)
        room.emit("player", user_id=str(user_id))  # исход игрока виден остальным
    if room.is_closed:
        return
    if two:
        _end(room, rules, timing, MatchFinish.FORFEIT, now_ms)
    elif len(match.active_ids()) < PAUSE_PLAYERS:
        _end(room, rules, timing, MatchFinish.COMPLETED, now_ms)


# ----------------------------------------------------------------------------- присутствие
def presence(
    room: Room,
    rules: MatchRules | None,
    timing: Timing,
    seen_ms: Mapping[UUID, int],
    now_ms: int,
) -> None:
    """Обновить присутствие по heartbeat (D10) и применить его к матчу.

    Args:
        seen_ms: Последний heartbeat игрока (``tick``); нет записи — время входа.
    """
    match = room.match
    for seat in list(room.seats):
        if seat.presence is Presence.LEFT:
            continue
        last = max(seen_ms.get(seat.user_id, 0), seat.joined_at_ms)
        fresh = now_ms - last < timing.presence_timeout_ms
        player = match.players.get(seat.user_id) if match is not None else None
        # Матч сверяется с состоянием игрока, а не только с переходом места: возврат
        # через join (место уже online) тоже снимает паузу.
        if fresh:
            room.reconnect(seat.user_id, now_ms)
            if player is not None and player.active and player.disconnected_at_ms is not None:
                _on_reconnect(room, seat.user_id, now_ms)
        else:
            room.disconnect(seat.user_id)
            if player is not None and player.active and player.disconnected_at_ms is None:
                _on_disconnect(room, seat.user_id, now_ms)
    _grace(room, rules, timing, now_ms)


def rejoin(room: Room, user_id: UUID, now_ms: int) -> None:
    """Участник вернулся по ссылке (join): heartbeat с текущего момента, пауза снимается."""
    seat = room.seat_of(user_id)
    if seat is None or seat.presence is Presence.LEFT:
        return
    seat.joined_at_ms = now_ms
    room.reconnect(user_id, now_ms)
    match = room.match
    player = match.players.get(user_id) if match is not None else None
    if player is not None and player.active and player.disconnected_at_ms is not None:
        _on_reconnect(room, user_id, now_ms)


def _on_disconnect(room: Room, user_id: UUID, now_ms: int) -> None:
    match = room.match
    if match is None or not room.in_match:
        return
    player = match.players.get(user_id)
    if player is None or not player.active:
        return
    player.disconnected_at_ms = now_ms
    if len(match.active_ids()) == PAUSE_PLAYERS and room.phase is not Phase.PAUSED:
        remaining = (
            None if room.phase_ends_at_ms is None else max(0, room.phase_ends_at_ms - now_ms)
        )
        match.paused = Paused(phase=room.phase, remaining_ms=remaining)
        match.paused_at_ms = now_ms
        room.set_phase(
            Phase.PAUSED,
            now_ms + match.grace_ms,
            round_index=match.round_index if match.contest else None,
            level=match.contest.level if match.contest else None,
            rounds_total=match.rounds_total,
        )


def _on_reconnect(room: Room, user_id: UUID, now_ms: int) -> None:
    match = room.match
    if match is None:
        return
    player = match.players.get(user_id)
    if player is None or not player.active:
        return
    player.disconnected_at_ms = None
    if room.phase is not Phase.PAUSED or match.paused is None:
        return
    if any(p.disconnected_at_ms is not None for p in match.players.values() if p.active):
        return
    paused, shift = match.paused, now_ms - (match.paused_at_ms or now_ms)
    match.paused = None
    match.paused_at_ms = None
    if match.contest is not None:
        c = match.contest
        match.contest = replace(
            c,
            level_opened_at_ms=_shift(c.level_opened_at_ms, shift),
            level_ends_at_ms=_shift(c.level_ends_at_ms, shift),
        )
    ends = None if paused.remaining_ms is None else now_ms + paused.remaining_ms
    room.set_phase(
        paused.phase,
        ends,
        round_index=match.round_index if paused.phase is not Phase.COUNTDOWN else None,
        level=match.contest.level if match.contest and paused.phase is Phase.LEVEL_OPEN else None,
        rounds_total=match.rounds_total,
    )


def _shift(value: int | None, delta: int) -> int | None:
    return None if value is None else value + delta


def _grace(room: Room, rules: MatchRules | None, timing: Timing, now_ms: int) -> None:
    """Party: отключившийся дольше ``disconnect_grace_s`` выбывает (очки сохраняются)."""
    match = room.match
    if match is None or rules is None or not room.in_match or room.phase is Phase.PAUSED:
        return
    for player in list(match.players.values()):
        if (
            player.active
            and player.disconnected_at_ms is not None
            and now_ms - player.disconnected_at_ms >= match.grace_ms
        ):
            _drop(room, rules, timing, player.user_id, finish=PlayerFinish.LEFT, now_ms=now_ms)
            if room.phase in (Phase.SUMMARY, Phase.CLOSED):
                return


def _tech_win(room: Room, rules: MatchRules, timing: Timing, now_ms: int) -> None:
    """Пауза истекла: отключившийся — ``tech_loss``, соперник — ``tech_win``."""
    match = _match(room)
    for player in match.players.values():
        if player.active and player.disconnected_at_ms is not None:
            player.finish = PlayerFinish.TECH_LOSS
            if match.contest is not None:
                match.contest = rules.leave(match.contest, _pid(player.user_id))
            seat = room.seat_of(player.user_id)
            if seat is not None:
                seat.presence = Presence.LEFT
    for player in match.players.values():
        if player.active:
            player.finish = PlayerFinish.TECH_WIN
    match.paused = None
    _end(room, rules, timing, MatchFinish.TECH_WIN, now_ms)


# ----------------------------------------------------------------------------- итог
def abort(room: Room, rules: MatchRules | None, timing: Timing, reason: str, now_ms: int) -> None:
    """Прервать (админ): матч — ``aborted``, комната — ``closed``."""
    if room.is_closed:
        raise MatchConflictError("комната уже закрыта", code=errors.ROOM_CLOSED)
    match = room.match
    if match is not None and match.finish is None and rules is not None:
        match.abort_reason = reason
        _end(room, rules, timing, MatchFinish.ABORTED, now_ms)
    room.close(reason, now_ms)


def _end(room: Room, rules: MatchRules, timing: Timing, finish: MatchFinish, now_ms: int) -> None:
    match = _match(room)
    if match.finish is not None:
        return
    match.finish = finish
    match.finished_at_ms = now_ms
    match.paused = None
    match.clue = None
    left = {
        _pid(p.user_id)
        for p in match.players.values()
        if p.finish in (PlayerFinish.LEFT, PlayerFinish.FORFEIT, PlayerFinish.TECH_LOSS)
    }
    scores = {_pid(p.user_id): p.score for p in match.players.values()}
    placements = rules.rank(scores, left)
    standings: list[Standing] = []
    for placement in placements:
        uid = UUID(placement.player_id)
        player = match.players[uid]
        if player.finish is None:
            player.finish = PlayerFinish.COMPLETED
        outcome = placement.outcome.value if placement.outcome is not None else None
        standings.append(
            Standing(
                user_id=uid,
                slot=player.slot,
                score=player.score,
                place=placement.place,
                outcome=outcome,
                finish=player.finish,
            )
        )
    match.standings = tuple(standings)
    room.remember((r.answer.media_item_id for r in match.plan), timing.memory_limit)
    room.touch(now_ms)
    room.set_phase(Phase.SUMMARY, now_ms + timing.idle_ms, round_index=None, level=None)
    room.emit("summary", match_id=str(match.match_id))


# ----------------------------------------------------------------------------- реванш
def vote_rematch(room: Room, user_id: UUID, now_ms: int) -> bool:
    """Голос за реванш; ``True`` — согласны все оставшиеся (пора создавать реванш)."""
    room.require_phase(Phase.SUMMARY)
    room.require_seat(user_id)
    room.touch(now_ms)
    if user_id not in room.rematch_votes:
        room.rematch_votes.add(user_id)
        room.emit("lobby")
    remaining = {s.user_id for s in room.seats if s.presence is not Presence.LEFT}
    return room.rematch_code is None and remaining <= room.rematch_votes


def back_to_lobby(room: Room, now_ms: int) -> None:
    """Реванш кастомного лобби: та же комната (и её память) снова в лобби."""
    match = room.match
    room.last_match_id = match.match_id if match is not None else room.last_match_id
    room.match = None
    room.seats = [s for s in room.seats if s.presence is not Presence.LEFT]
    for seat in room.seats:
        seat.ready = False
    room.rematch_votes.clear()
    room.rematch_code = room.code
    room.touch(now_ms)
    room.set_phase(Phase.LOBBY, None, round_index=None, level=None)
    room.emit("lobby")
