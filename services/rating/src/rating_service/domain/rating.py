"""Рейтинг игрока ``Rating(user, ladder, season)`` (ADR 0037, 0039)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from gv_core.domain import AggregateRoot
from rating_service.domain.elo import EloDelta, Outcome, Side, round_half_away
from rating_service.domain.events import RatingChanged
from rating_service.domain.ladder import LadderConfig, SoftReset


def soft_reset_value(old: int, reset: SoftReset) -> int:
    """Рейтинг после мягкого сброса: ``target + ratio × (old − target)``, округление от нуля."""
    return round_half_away(reset.target + reset.ratio * (old - reset.target))


class Rating(AggregateRoot[tuple[UUID, str, UUID]]):
    """Рейтинг игрока в ladder-е и сезоне.

    Attributes:
        rating: Текущий рейтинг (снизу не ограничен — пола в конфиге нет).
        matches_total: Рейтинговые матчи для калибровки (не обнуляются сбросом сезона).
        season_matches: Матчи текущего сезона.
        event_version: Версия ``RatingChanged`` по ``(user, ladder)``; новые сезоны наследуют.
    """

    def __init__(
        self,
        *,
        user_id: UUID,
        ladder: str,
        season_id: UUID,
        rating: int,
        matches_total: int,
        season_matches: int,
        event_version: int,
        updated_at: datetime,
    ) -> None:
        super().__init__((user_id, ladder, season_id))
        self.user_id = user_id
        self.ladder = ladder
        self.season_id = season_id
        self.rating = rating
        self.matches_total = matches_total
        self.season_matches = season_matches
        self.event_version = event_version
        self.updated_at = updated_at

    @classmethod
    def start(cls, user_id: UUID, season_id: UUID, config: LadderConfig, now: datetime) -> Rating:
        """Новый игрок ladder-а: стартовый рейтинг из конфига."""
        return cls(
            user_id=user_id,
            ladder=config.key,
            season_id=season_id,
            rating=config.elo.start,
            matches_total=0,
            season_matches=0,
            event_version=0,
            updated_at=now,
        )

    def side(self, outcome: Outcome, *, left: bool) -> Side:
        """Вход расчёта Эло для этого игрока."""
        return Side(
            rating=self.rating, matches_total=self.matches_total, outcome=outcome, left=left
        )

    def apply_match(
        self,
        config: LadderConfig,
        delta: EloDelta,
        *,
        match_id: UUID,
        queue_format: str,
        outcome: Outcome,
        at: datetime,
    ) -> RatingChanged:
        """Применить дельту матча и зафиксировать ``RatingChanged``.

        Звание в событии и истории скрыто (``None``), пока игрок в калибровке и в конфиге
        ``calibration.hide_tier``; рейтинг при этом реальный — он нужен подбору.
        """
        old = self.rating
        old_tier = self._visible_tier(config, old, self.matches_total)
        self.rating += delta.total
        self.matches_total += 1
        self.season_matches += 1
        self.event_version += 1
        self.updated_at = at
        event = RatingChanged(
            aggregate_id=str(self.user_id),
            user_id=self.user_id,
            ladder=self.ladder,
            format=queue_format,
            season_id=self.season_id,
            match_id=match_id,
            version=self.event_version,
            old=old,
            new=self.rating,
            delta=delta.total,
            tier=self._visible_tier(config, self.rating, self.matches_total),
            old_tier=old_tier,
            calibrating=config.is_calibrating(self.matches_total),
            calibration_left=config.calibration_left(self.matches_total),
            matches_played=self.season_matches,
            outcome=outcome.value,
            leaver_penalty=delta.leaver_penalty,
            at=at,
        )
        self.record(event)
        return event

    def carry_over(self, season_id: UUID, reset: SoftReset, now: datetime) -> Rating:
        """Рейтинг в новом сезоне: мягкий сброс, калибровка и версия событий сохраняются."""
        return Rating(
            user_id=self.user_id,
            ladder=self.ladder,
            season_id=season_id,
            rating=soft_reset_value(self.rating, reset),
            matches_total=self.matches_total,
            season_matches=0,
            event_version=self.event_version,
            updated_at=now,
        )

    @staticmethod
    def _visible_tier(config: LadderConfig, rating: int, matches_total: int) -> str | None:
        if config.calibration.hide_tier and config.is_calibrating(matches_total):
            return None
        return config.tier_for(rating).key
