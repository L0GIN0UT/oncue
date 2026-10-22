"""Состояние раунда онлайн-матча (ADR 0032, D3 плана этапа 4).

Матч — несколько игроков на одной загадке. Общий для всех — открытый уровень и его окно;
у каждого игрока — свой ``RoundState`` (ходы, попытки, частичный ответ), правила которого
задаёт ``ContestRule`` через ``RoundRules``. Состояние неизменяемо, переходы — чистые
функции плагина ``ContestRule``; время приходит аргументом ``now_ms`` (часы — у match).

Сигнатуры зафиксированы шагом 0a; реализации переходов — ``builtin/contests.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from gv_game_kernel.round import RoundState


class PlayerStatus(StrEnum):
    """Статус игрока в раунде, который видят все участники (без текста ответа).

    ``thinking`` — ещё отвечает на открытом уровне; ``answered`` — ответил и ждёт
    закрытия уровня (``parallel``) или исчерпал попытки уровня; ``missed`` — раунд для него
    кончился без угадывания; ``guessed`` — угадал точно (или частично — по итогу).
    """

    THINKING = "thinking"
    ANSWERED = "answered"
    MISSED = "missed"
    GUESSED = "guessed"


@dataclass(frozen=True, slots=True)
class PlayerRound:
    """Раунд одного игрока внутри матча.

    Attributes:
        state: Ходы игрока по правилам ``ContestRule.round_rules``.
        answered_at_ms: Когда игрок дал точный (или частичный) ответ — для очков за
            скорость (``party_speed``): ``t`` считается от открытия уровня.
        active: Игрок участвует в раунде (``False`` — вышел: его раунд ``void``).
        elapsed_ms: ``t`` для ``party_speed``: от открытия уровня до ответа, который
            оценивается (точного, иначе первого частичного); добавлено потоком A.
        window_ms: ``T`` для ``party_speed``: окно уровня этого ответа; добавлено потоком A.
    """

    state: RoundState = field(default_factory=RoundState)
    answered_at_ms: int | None = None
    active: bool = True
    elapsed_ms: int | None = None
    window_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ContestRoundState:
    """Общее состояние раунда матча.

    Attributes:
        level: Открытый уровень загадки (с 1); ``0`` — раунд ещё не начался.
        level_opened_at_ms: Когда открыт текущий уровень (серверное время, мс).
        level_ends_at_ms: Дедлайн окна текущего уровня (``session.level_windows_s``).
        level_locked: Уровень закрыт для всех досрочно (``race``: первый ``exact``).
        players: Раунд каждого игрока по его идентификатору (строка ``user_id``).
        finished: Раунд завершён для всех.
    """

    level: int = 0
    level_opened_at_ms: int | None = None
    level_ends_at_ms: int | None = None
    level_locked: bool = False
    players: Mapping[str, PlayerRound] = field(default_factory=dict)
    finished: bool = False


@dataclass(frozen=True, slots=True)
class PlayerRoundView:
    """Что о раунде игрока видят другие участники (античит §12 плана).

    Attributes:
        player_id: Игрок.
        status: Статус без текста ответа.
        level: Уровень, на котором игрок угадал (после закрытия уровня), иначе ``None``.
    """

    player_id: str
    status: PlayerStatus
    level: int | None = None


__all__ = ["ContestRoundState", "PlayerRound", "PlayerRoundView", "PlayerStatus"]
