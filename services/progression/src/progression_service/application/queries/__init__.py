"""Чтение прогресса и кошелька для публичного API (поток P1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from gv_core.clock import Clock
from gv_core.errors import NotFoundError
from progression_service.application.accrual.common import published_config
from progression_service.application.ports import UnitOfWorkFactory
from progression_service.domain import levels
from progression_service.domain.ledger import Bucket, Entry

USER_NOT_FOUND = "progression.user_not_found"


@dataclass(frozen=True, slots=True)
class StreakView:
    """Серия дейлика домена."""

    domain: str
    current: int
    best: int
    last_day: date | None


@dataclass(frozen=True, slots=True)
class ProgressView:
    """Свой прогресс (``MyProgress`` без бустов — их добавит P2)."""

    level: int
    max_level: int
    total_xp: int
    xp_in_level: int
    xp_to_next: int | None
    solo_xp_today: int
    solo_xp_cap: int
    streaks: tuple[StreakView, ...]
    config_version: int


@dataclass(frozen=True, slots=True)
class PublicProgressView:
    """Публичный прогресс игрока."""

    user_id: UUID
    level: int
    total_xp: int


@dataclass(frozen=True, slots=True)
class WalletView:
    """Кошелёк: балансы бакетов и последние проводки."""

    earned: int
    paid: int
    recent: tuple[Entry, ...]


class ProgressQueries:
    """Чтение прогресса и кошелька."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, *, recent_limit: int) -> None:
        self._uow = uow
        self._clock = clock
        self._recent_limit = recent_limit

    async def my_progress(self, user_id: UUID) -> ProgressView:
        """Прогресс игрока; у того, кто ещё ничего не получал, — первый уровень.

        Raises:
            ConfigUnavailableError: нет опубликованного конфига.
        """
        async with self._uow() as uow:
            published = await published_config(uow)
            cfg = published.config
            row = await uow.progress.get(user_id)
            total = row.total_xp if row else 0
            pos = levels.progress(cfg.levels, total, floor_level=row.level if row else 1)
            today = self._clock.now().astimezone(cfg.tz).date()
            counters = await uow.counters.get(user_id, today)
            streaks = await uow.streaks.all(user_id)
            return ProgressView(
                level=pos.level,
                max_level=cfg.levels.max_level,
                total_xp=total,
                xp_in_level=pos.xp_in_level,
                xp_to_next=pos.xp_to_next,
                solo_xp_today=counters.solo_xp,
                solo_xp_cap=cfg.xp.solo_daily_cap,
                streaks=tuple(
                    StreakView(domain, s.current, s.best, s.last_day)
                    for domain, s in sorted(streaks.items())
                ),
                config_version=published.version,
            )

    async def my_wallet(self, user_id: UUID) -> WalletView:
        """Балансы и последние проводки."""
        async with self._uow() as uow:
            balances = await uow.balances.all(user_id)
            recent = await uow.ledger.recent_currency(user_id, self._recent_limit)
        return WalletView(
            earned=balances.get(Bucket.EARNED, 0),
            paid=balances.get(Bucket.PAID, 0),
            recent=tuple(recent),
        )

    async def public_progress(self, user_id: UUID) -> PublicProgressView:
        """Публичный прогресс.

        Raises:
            NotFoundError: ``progression.user_not_found`` — записи нет или профиль скрыт.
        """
        async with self._uow() as uow:
            row = await uow.progress.get(user_id)
        if row is None or row.hidden:
            raise NotFoundError("игрок не найден", code=USER_NOT_FOUND)
        return PublicProgressView(user_id=user_id, level=row.level, total_xp=row.total_xp)
