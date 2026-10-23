"""Задания: периоды, детерминированное назначение снимком, прогресс (ADR 0042, 0044; R1, R8).

Назначение - снимок на период: ключ, цель, награда и версия конфига фиксируются в момент
назначения, смена конфига действует со следующего периода. Выбор из пула идёт по весам и
детерминирован от ``(user_id, период, начало периода, версия конфига)``: повторный расчёт даёт тот
же набор (гонка двух событий не плодит разные наборы). Времени и ``random`` здесь нет.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from gv_core.random import SeededRandom
from progression_service.domain.config import QuestDef, Reward
from progression_service.domain.metrics import MetricUpdate, merge

DAILY = "daily"
WEEKLY = "weekly"
ONE_TIME = "one_time"


def period_start(period: str, day: date) -> date:
    """Начало периода, в который попадает локальный ``day``.

    Дневной - сами сутки, недельный - понедельник недели, разовый - день назначения.
    """
    if period == WEEKLY:
        return day - timedelta(days=day.weekday())
    return day


def period_end(period: str, start: date) -> date | None:
    """Последние сутки периода включительно; у разовых - ``None``."""
    if period == DAILY:
        return start
    if period == WEEKLY:
        return start + timedelta(days=6)
    return None


def select_quests(
    pool: Sequence[QuestDef], count: int, *, user_id: UUID, period: str, start: date, version: int
) -> list[QuestDef]:
    """Выбрать ``count`` заданий из пула по весам без повторов, детерминированно.

    Пул меньше ``count`` - достаётся весь пул. Результат в порядке пула.
    """
    ordered = sorted(pool, key=lambda q: q.key)
    take = min(max(0, count), len(ordered))
    if take == len(ordered):
        return [q for q in pool if q in ordered]
    rng = SeededRandom(f"quests:{user_id}:{period}:{start.isoformat()}:{version}".encode())
    left = list(ordered)
    chosen: list[QuestDef] = []
    for _ in range(take):
        total = sum(q.weight for q in left)
        roll = rng.randrange(total)
        for quest in left:
            if roll < quest.weight:
                chosen.append(quest)
                left.remove(quest)
                break
            roll -= quest.weight
    picked = {q.key for q in chosen}
    return [q for q in pool if q.key in picked]


@dataclass(frozen=True, slots=True)
class QuestSnapshot:
    """Задание, назначенное игроку на период (неизменяемый снимок конфига)."""

    key: str
    period: str
    period_start: date
    title: str
    description: str
    metric: str
    target: int
    domain: str | None
    reward: Reward
    config_version: int

    @classmethod
    def of(cls, quest: QuestDef, start: date, version: int) -> QuestSnapshot:
        """Снимок задания пула на период."""
        return cls(
            key=quest.key,
            period=quest.period,
            period_start=start,
            title=quest.title,
            description=quest.description,
            metric=quest.metric,
            target=quest.target,
            domain=quest.domain,
            reward=quest.reward,
            config_version=version,
        )

    def accepts(self, update: MetricUpdate) -> bool:
        """Двигает ли изменение метрики это задание (метрика и ограничение домена)."""
        if update.metric != self.metric:
            return False
        return self.domain is None or update.domain == self.domain

    def advance(self, progress: int, update: MetricUpdate) -> int:
        """Новый прогресс: не больше цели."""
        return min(self.target, merge(progress, update))


def op_id(user_id: UUID, key: str, start: date) -> str:
    """Идентификатор проводок награды: один на ``(игрок, задание, период)``."""
    return f"quest:{user_id}:{key}:{start.isoformat()}"
