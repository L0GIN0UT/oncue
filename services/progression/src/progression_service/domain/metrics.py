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
# 5d (ADR 0055): триггеры находок сезонных событий.
DUEL_WIN = "duel_win"
WEEKLY_TOP_PLACE = "weekly_top_place"
WHEEL_SPIN_STREAK = "wheel_spin_streak"
ITEM_EQUIPPED = "item_equipped"
COLLECTION_PLAYED = "collection_played"

COUNTERS = frozenset(
    {
        ROUNDS_PLAYED,
        CORRECT_ANSWERS,
        WINS,
        ONLINE_MATCHES,
        DAILY_COMPLETED,
        COLLECTION_PUBLISHED,
        DUEL_WIN,
        WEEKLY_TOP_PLACE,
        COLLECTION_PLAYED,
    }
)
"""Метрики-счётчики: событие прибавляет величину."""

LEVELS = frozenset({DAILY_STREAK, SEASON_TIER, WHEEL_SPIN_STREAK, ITEM_EQUIPPED})
"""Метрики-уровни: событие приносит значение, хранится максимум."""

KNOWN = COUNTERS | LEVELS

EVENT_ONLY = frozenset({WEEKLY_TOP_PLACE, ITEM_EQUIPPED})
"""Метрики с параметрами (место, слот, предмет): без них значение бессмысленно, поэтому они
только для триггеров находок событий; заданиям и бейджам конфиг их не разрешает."""


def is_known(metric: str) -> bool:
    """Есть ли метрика в реестре."""
    return metric in KNOWN


def is_config_metric(metric: str) -> bool:
    """Можно ли ссылаться на метрику в заданиях и бейджах конфига."""
    return metric in KNOWN and metric not in EVENT_ONLY


@dataclass(frozen=True, slots=True)
class MetricUpdate:
    """Изменение метрики одним событием.

    Attributes:
        metric: Ключ метрики из реестра.
        amount: У счётчика — прибавка (≥ 1), у уровня — достигнутое значение.
        domain: Домен события (``music``/``film``) для заданий с ограничением; ``None`` —
            у события домена нет.
        place: Место в топе недели (``weekly_top_place``).
        slot: Слот надетого предмета (``item_equipped``).
        item_key: Ключ надетого предмета (``item_equipped``).
        collection_id: Сборник сессии строкой UUID (``collection_played``).
        collection_tags: Теги сыгранного сборника (``collection_played``, ADR 0067); пусто - у
            сборника их нет или событие старого формата.
    """

    metric: str
    amount: int
    domain: str | None = None
    place: int | None = None
    slot: str | None = None
    item_key: str | None = None
    collection_id: str | None = None
    collection_tags: tuple[str, ...] = ()

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
