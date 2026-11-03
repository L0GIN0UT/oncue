"""Учёт угадываний: события итогов игр → агрегаты по контенту (ADR 0076)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import structlog

from catalog_service.application.ports import ContentStatsRepository
from catalog_service.domain.content_stats import (
    RoundObservation,
    StatsRules,
    aggregate,
    observations_from_game_finished,
    observations_from_match_finished,
)

log = structlog.get_logger(__name__)

GAME_FINISHED = "game.GameFinished"
MATCH_FINISHED = "match.MatchFinished"


class RecordRoundStats:
    """Прибавить итоги раундов события к счётчикам.

    Идемпотентность — на стороне вызывающего: обработчик работает внутри ``EventProcessor``
    (inbox и инкремент в одной транзакции), поэтому повторная доставка того же события
    счётчики не меняет. Сами игровые события публикуются один раз (``GameFinished`` — при
    завершении сессии, ``MatchFinished`` — UNIQUE ``match_id``).
    """

    def __init__(self, stats: ContentStatsRepository, rules: StatsRules) -> None:
        self._stats = stats
        self._rules = rules
        self._parsers: Mapping[str, Callable[[Mapping[str, Any]], list[RoundObservation]]] = {
            GAME_FINISHED: observations_from_game_finished,
            MATCH_FINISHED: lambda p: observations_from_match_finished(p, rules),
        }

    async def __call__(self, event_type: str, payload: Mapping[str, Any]) -> int:
        """Учесть событие; вернуть число учтённых показов.

        Raises:
            InvalidStatsEventError: payload не по контракту (``PermanentError`` выше).
            KeyError: ``event_type`` не поддерживается (ошибка регистрации).
        """
        observations = self._parsers[event_type](payload)
        if not observations:
            return 0
        await self._stats.add(aggregate(observations, self._rules))
        log.info("content_stats.recorded", event_type=event_type, shown=len(observations))
        return len(observations)
