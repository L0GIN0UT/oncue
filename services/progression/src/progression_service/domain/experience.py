"""Начисление опыта: чистые функции от (результат, конфиг версии, дневные счётчики) (ADR 0042).

Опыт — за участие, не за результат (D3). Ранг и рейтинг на опыт не влияют, ranked и кастом равны.
Суммы берутся из ``XpConfig``; здесь только правила. Времени и случайности нет: сутки приходят
параметром, множитель буста — снаружи (порт ``RewardHook``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from progression_service.domain.config import DailyXp, PartyXp, SoloRoundXp, XpConfig


class Finish(StrEnum):
    """Как игрок закончил матч (``match.MatchFinished.players[].finish``)."""

    COMPLETED = "completed"
    FORFEIT = "forfeit"
    LEFT = "left"
    TECH_WIN = "tech_win"
    TECH_LOSS = "tech_loss"


class MatchFormat(StrEnum):
    """Вид сессии (``MatchFinished.format``)."""

    DUEL = "duel"
    PARTY = "party"
    LOBBY = "lobby"


class Outcome(StrEnum):
    """Исход дуэли."""

    WIN = "win"
    LOSS = "loss"
    DRAW = "draw"


# Игрок, ушедший из матча, награды не получает; tech_loss — техническое поражение того, кто
# перестал играть (как у рейтинга: ``LEAVER_FINISHES``).
NO_REWARD_FINISHES = frozenset({Finish.FORFEIT, Finish.LEFT, Finish.TECH_LOSS})


@dataclass(frozen=True, slots=True)
class OnlineResult:
    """Итог игрока в онлайн-матче (вход начисления, не событие).

    Attributes:
        format: Дуэль, party или lobby.
        finish: Как закончил.
        outcome: Исход дуэли; ``None`` в матче на 3+ игроков.
        place: Место с ничьими (1, 1, 3).
        rounds_total: Раундов в матче.
        rounds_played: Раундов, в которых игрок участвовал.
        rounds_won: Раундов, выигранных игроком (строго больше очков, чем у остальных).
        aborted: Матч прерван.
    """

    format: MatchFormat
    finish: Finish
    outcome: Outcome | None
    place: int
    rounds_total: int
    rounds_played: int
    rounds_won: int
    aborted: bool = False

    @property
    def rewarded(self) -> bool:
        """Получает ли игрок награду вообще (не прерван, не вышел)."""
        return not self.aborted and self.finish not in NO_REWARD_FINISHES

    def result_bonuses_allowed(self, min_rounds_ratio: float) -> bool:
        """Бонусы за результат (победа, место, первая победа дня) — антиабьюз R5.

        Нужны завершённый матч (не ``tech_win``) и доля сыгранных раундов не ниже порога.
        Матч без раундов (``rounds_total <= 0``) долю не выполнил: бонусов нет, участие — обычное
        (иначе бонус победы можно получить на пустых данных).
        """
        if not self.rewarded or self.finish is not Finish.COMPLETED:
            return False
        if self.rounds_total <= 0:
            return False
        return self.rounds_played / self.rounds_total >= min_rounds_ratio

    def is_win(self) -> bool:
        """Победа для «первой победы дня» (R4): победа в дуэли или место 1; ничья не в счёт."""
        if self.format is MatchFormat.DUEL:
            return self.outcome is Outcome.WIN
        return self.place == 1 and self.outcome is not Outcome.DRAW


@dataclass(frozen=True, slots=True)
class OnlineXp:
    """Опыт за онлайн-матч до буста.

    Attributes:
        match_xp: Участие, раунды, бонус за победу или место.
        first_win_eligible: Матч годится для бонуса «первая победа дня».
    """

    match_xp: int
    first_win_eligible: bool


def solo_round_xp(cfg: SoloRoundXp, points: int) -> int:
    """Опыт за один соло-раунд: ``base + очки // divisor``, не больше ``max``."""
    return min(cfg.max, cfg.base + max(0, points) // cfg.score_divisor)


def solo_session_xp(cfg: XpConfig, round_points: Sequence[int]) -> int:
    """Опыт за соло-сессию до дневного потолка: сумма по раундам."""
    return sum(solo_round_xp(cfg.solo_round, p) for p in round_points)


def daily_xp(cfg: DailyXp, streak: int) -> int:
    """Опыт за дейлик: ``base + per_streak · серия``, не больше ``max``."""
    return min(cfg.max, cfg.base + cfg.per_streak * max(0, streak))


def party_place_bonus(cfg: PartyXp, place: int) -> int:
    """Бонус за место в party/lobby: по списку подряд с первого, остальным — прочий."""
    if 1 <= place <= len(cfg.place_bonus):
        return cfg.place_bonus[place - 1]
    return cfg.other_place_bonus


def online_xp(cfg: XpConfig, result: OnlineResult, min_rounds_ratio: float) -> OnlineXp:
    """Опыт за онлайн-матч (дуэль, party, lobby).

    Выход, ``forfeit`` и прерванный матч — 0. ``tech_win`` — участие и раунды без бонуса за победу.
    """
    if not result.rewarded:
        return OnlineXp(0, first_win_eligible=False)
    bonuses = result.result_bonuses_allowed(min_rounds_ratio)
    if result.format is MatchFormat.DUEL:
        total = cfg.duel.participation + cfg.duel.per_round_won * result.rounds_won
        if bonuses and result.outcome is Outcome.WIN:
            total += cfg.duel.win_bonus
    else:
        total = cfg.party.participation
        if bonuses:
            total += party_place_bonus(cfg.party, result.place)
    return OnlineXp(total, first_win_eligible=bonuses and result.is_win())


def apply_cap(amount: int, used: int, cap: int) -> int:
    """Сколько из ``amount`` влезает в потолок суток при уже использованных ``used``."""
    return max(0, min(amount, cap - used))
