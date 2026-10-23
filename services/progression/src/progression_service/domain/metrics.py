"""Реестр метрик заданий и бейджей (ADR 0042, 0044).

Метрика — именованная величина игрока, которую двигают события. Задание и бейдж ссылаются на
метрику по ключу; неизвестный ключ в конфиге отклоняется при публикации
(``progression.config_invalid``). Новая метрика = строка здесь + источник события в application.

Два вида метрик:

* **счётчики** растут на величину события (раунды, угаданные, победы…);
* **уровни** хранят достигнутое значение и только растут (серия дейлика, звание сезона):
  обновление берёт максимум, поэтому повтор и опоздавшее событие безвредны.
"""

from __future__ import annotations

from dataclasses import dataclass

ROUNDS_PLAYED = "rounds_played"
CORRECT_ANSWERS = "correct_answers"
WINS = "wins"
ONLINE_MATCHES = "online_matches"
DAILY_COMPLETED = "daily_completed"
COLLECTION_PUBLISHED = "collection_published"
DAILY_STREAK = "daily_streak"
SEASON_TIER = "season_tier"

COUNTERS = frozenset(
    {ROUNDS_PLAYED, CORRECT_ANSWERS, WINS, ONLINE_MATCHES, DAILY_COMPLETED, COLLECTION_PUBLISHED}
)
"""Метрики-счётчики: событие прибавляет величину."""

LEVELS = frozenset({DAILY_STREAK, SEASON_TIER})
"""Метрики-уровни: событие приносит значение, хранится максимум."""

KNOWN = COUNTERS | LEVELS


def is_known(metric: str) -> bool:
    """Есть ли метрика в реестре."""
    return metric in KNOWN


@dataclass(frozen=True, slots=True)
class MetricUpdate:
    """Изменение метрики одним событием.

    Attributes:
        metric: Ключ метрики из реестра.
        amount: У счётчика — прибавка (≥ 1), у уровня — достигнутое значение.
        domain: Домен события (``music``/``film``) для заданий с ограничением; ``None`` —
            у события домена нет.
    """

    metric: str
    amount: int
    domain: str | None = None

    def __post_init__(self) -> None:
        if not is_known(self.metric):
            raise ValueError(f"метрика {self.metric!r} не в реестре")
        if self.amount < 0:
            raise ValueError("величина метрики не может быть отрицательной")


def merge(current: int, update: MetricUpdate) -> int:
    """Новое значение метрики: счётчик складывается, уровень берёт максимум."""
    if update.metric in LEVELS:
        return max(current, update.amount)
    return current + update.amount
