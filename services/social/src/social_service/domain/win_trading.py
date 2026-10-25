"""Сигналы win-trading для модератора (ADR 0068, уточняет ADR 0038). Чистый домен.

Сигнал - не вердикт: он только показывает модератору числа и окно. Автосанкций, уведомлений
игроку и блокировок очереди нет. Пороги и окна приходят из ``WinTradingConfig`` (конфиг
social в БД), в коде их нет.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from social_service.domain.config import ForfeitSeriesConfig, PairRepeatConfig

KIND_PAIR = "pair_repeat"
KIND_FORFEIT_SERIES = "forfeit_series"
KIND_FORFEIT_WINDOW = "forfeit_window"
KINDS = (KIND_PAIR, KIND_FORFEIT_SERIES, KIND_FORFEIT_WINDOW)
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"
FINISH_FORFEIT = "forfeit"
OUTCOME_WIN = "win"
DUEL_SIZE = 2


@dataclass(frozen=True, slots=True)
class MatchFact:
    """Рейтинговая дуэль: пара (``user_low < user_high``), победитель и сдавшийся."""

    match_id: UUID
    user_low: UUID
    user_high: UUID
    winner_id: UUID | None
    forfeiter_id: UUID | None
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class Signal:
    """Подозрительная пара или серия сдач."""

    key: str
    kind: str
    user_id: UUID
    other_user_id: UUID | None
    count: int
    streak: int
    severity: str
    window_s: int
    first_at: datetime
    last_at: datetime
    details: Mapping[str, Any] = field(default_factory=dict)


def pair_of(first: UUID, second: UUID) -> tuple[UUID, UUID]:
    """Пара без порядка: меньший идентификатор первым."""
    return (first, second) if first < second else (second, first)


def fact_from_event(payload: Mapping[str, Any]) -> MatchFact | None:
    """Факт из ``match.MatchFinished`` или ``None``: не рейтинг, прервано, не дуэль 1×1."""
    players = payload["players"]
    ranked = payload.get("ranked")
    if ranked is None:  # поле необязательное в контракте: тогда рейтинговость - по ladder
        ranked = payload.get("ladder") is not None
    if not ranked or payload["aborted"] or len(players) != DUEL_SIZE:
        return None
    ids = [UUID(p["user_id"]) for p in players]
    if ids[0] == ids[1]:
        return None
    winners = [UUID(p["user_id"]) for p in players if p.get("outcome") == OUTCOME_WIN]
    forfeiters = [UUID(p["user_id"]) for p in players if p["finish"] == FINISH_FORFEIT]
    low, high = pair_of(ids[0], ids[1])
    return MatchFact(
        match_id=UUID(payload["match_id"]),
        user_low=low,
        user_high=high,
        winner_id=winners[0] if len(winners) == 1 else None,
        forfeiter_id=forfeiters[0] if len(forfeiters) == 1 else None,
        finished_at=datetime.fromisoformat(payload["finished_at"]),
    )


def _in_window(facts: Iterable[MatchFact], window_s: int) -> list[MatchFact]:
    """Факты окна от самого свежего (``facts`` - новые первыми)."""
    items = list(facts)
    if not items:
        return []
    border = items[0].finished_at - timedelta(seconds=window_s)
    return [f for f in items if f.finished_at >= border]


def evaluate_pair(facts: Sequence[MatchFact], cfg: PairRepeatConfig) -> Signal | None:
    """Повторы пары (в обе стороны) за окно; ``facts`` - матчи одной пары, новые первыми."""
    window = _in_window(facts, cfg.window_s)
    if len(window) < cfg.min_matches:
        return None
    newest = window[0]
    wins: dict[str, int] = {str(newest.user_low): 0, str(newest.user_high): 0}
    for f in window:
        if f.winner_id is not None:
            wins[str(f.winner_id)] += 1
    streak = 0
    if newest.winner_id is not None:
        for f in window:
            if f.winner_id != newest.winner_id:
                break
            streak += 1
    return Signal(
        key=f"{KIND_PAIR}:{newest.user_low}:{newest.user_high}",
        kind=KIND_PAIR,
        user_id=newest.user_low,
        other_user_id=newest.user_high,
        count=len(window),
        streak=streak,
        severity=SEVERITY_HIGH if streak >= cfg.strong_streak else SEVERITY_MEDIUM,
        window_s=cfg.window_s,
        first_at=window[-1].finished_at,
        last_at=newest.finished_at,
        details={"wins": wins},
    )


def evaluate_forfeit_series(
    facts: Sequence[MatchFact], user_id: UUID, cfg: ForfeitSeriesConfig
) -> Signal | None:
    """Сдачи подряд игрока против одного соперника; ``facts`` - матчи пары, новые первыми."""
    window = _in_window(facts, cfg.window_s)
    streak = 0
    for f in window:
        if f.forfeiter_id != user_id:
            break
        streak += 1
    if streak < cfg.streak_min:
        return None
    opponent = window[0].user_high if window[0].user_low == user_id else window[0].user_low
    forfeits = [f for f in window if f.forfeiter_id == user_id]
    return Signal(
        key=f"{KIND_FORFEIT_SERIES}:{user_id}:{opponent}",
        kind=KIND_FORFEIT_SERIES,
        user_id=user_id,
        other_user_id=opponent,
        count=len(forfeits),
        streak=streak,
        severity=SEVERITY_MEDIUM,
        window_s=cfg.window_s,
        first_at=forfeits[-1].finished_at,
        last_at=window[0].finished_at,
    )


def evaluate_forfeit_window(
    facts: Sequence[MatchFact], user_id: UUID, cfg: ForfeitSeriesConfig
) -> Signal | None:
    """Сдачи игрока за окно (любые соперники); ``facts`` - матчи игрока, новые первыми."""
    own = _in_window([f for f in facts if f.forfeiter_id == user_id], cfg.window_s)
    if len(own) < cfg.window_min:
        return None
    return Signal(
        key=f"{KIND_FORFEIT_WINDOW}:{user_id}",
        kind=KIND_FORFEIT_WINDOW,
        user_id=user_id,
        other_user_id=None,
        count=len(own),
        streak=0,
        severity=SEVERITY_MEDIUM,
        window_s=cfg.window_s,
        first_at=own[-1].finished_at,
        last_at=own[0].finished_at,
        details={
            "opponents": len({f.user_high if f.user_low == user_id else f.user_low for f in own})
        },
    )
