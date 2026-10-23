"""Подбор рейтинговой дуэли: окно рейтинга и жадный батч (ADR 0038, без IO).

Параметры окна и интервалов приходят из ``rating.LadderPublished.matchmaking`` — здесь нет ни
одного игрового числа. Пара допустима, только если разница рейтингов попадает в окно **обоих**
игроков: окно каждого растёт со временем его ожидания.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import floor
from uuid import UUID

FORMAT_ANSWER: dict[str, str] = {"classic": "classic", "lite": "pick"}
"""Формат очереди → ``answer.format`` рейтингового шаблона (ADR 0038)."""

MS = 1000

SEARCHING = "searching"
"""Игрок ищет соперника (в ZSET очереди)."""
RESERVED = "reserved"
"""Пара зарезервирована, комната создаётся: клиенту это тот же поиск."""
MATCHED = "matched"
"""Комната создана: у записи есть ``room_code``."""


def queue_format_of(answer_format: str) -> str | None:
    """Формат очереди по ``answer.format`` шаблона; ``None`` — такого формата очереди нет."""
    for queue_format, answer in FORMAT_ANSWER.items():
        if answer == answer_format:
            return queue_format
    return None


@dataclass(frozen=True, slots=True)
class MatchmakingConfig:
    """Подбор ladder-а (``LadderPublished.matchmaking``).

    Attributes:
        base: Начальное окно ±, очков.
        step: Прирост окна за шаг.
        step_every_s: Длина шага, с.
        max: Потолок окна ±.
        slow_hint_after_s: Через сколько показать «ищем дольше обычного».
        interval_s: Период батчевого матчера.
    """

    base: int
    step: int
    step_every_s: int
    max: int
    slow_hint_after_s: int
    interval_s: int

    def window_at(self, wait_s: int) -> int:
        """Окно ± для игрока, ждущего ``wait_s`` секунд."""
        steps = max(0, wait_s) // self.step_every_s
        return min(self.max, self.base + self.step * steps)

    def is_slow(self, wait_s: int) -> bool:
        """Пора показать «ищем дольше обычного» (игрок остаётся в очереди)."""
        return wait_s >= self.slow_hint_after_s


@dataclass(frozen=True, slots=True)
class Candidate:
    """Игрок в очереди для батча."""

    user_id: UUID
    rating: int
    entered_at_ms: int

    def wait_s(self, now_ms: int) -> int:
        """Сколько секунд игрок ждёт."""
        return max(0, now_ms - self.entered_at_ms) // MS


def pair_batch(
    candidates: Sequence[Candidate], config: MatchmakingConfig, now_ms: int
) -> list[tuple[Candidate, Candidate]]:
    """Жадный подбор пар: ближайшие по рейтингу первыми, при равенстве — дольше ждущие.

    Пара обязана попасть в окно обоих игроков. Игрок входит максимум в одну пару; в паре
    первый — тот, кто ждёт дольше (он становится хостом комнаты). Результат детерминирован.
    """
    ordered = sorted(candidates, key=lambda c: (c.rating, c.entered_at_ms, c.user_id.int))
    windows = [config.window_at(c.wait_s(now_ms)) for c in ordered]
    edges: list[tuple[int, int, int, int]] = []
    for i, a in enumerate(ordered):
        for j in range(i + 1, len(ordered)):
            diff = ordered[j].rating - a.rating
            if diff > config.max:
                break  # дальше только больше: никакое окно не достанет
            if diff <= min(windows[i], windows[j]):
                oldest = min(a.entered_at_ms, ordered[j].entered_at_ms)
                edges.append((diff, oldest, i, j))
    edges.sort()
    taken: set[int] = set()
    pairs: list[tuple[Candidate, Candidate]] = []
    for _, _, i, j in edges:
        if i in taken or j in taken:
            continue
        taken.update((i, j))
        first, second = sorted((ordered[i], ordered[j]), key=lambda c: (c.entered_at_ms, c.rating))
        pairs.append((first, second))
    return pairs


def soft_reset(rating: int, *, target: int, ratio: float) -> int:
    """Мягкий сброс сезона: ``target + ratio × (rating − target)`` (ADR 0039), с округлением."""
    return floor(target + ratio * (rating - target) + 0.5)
