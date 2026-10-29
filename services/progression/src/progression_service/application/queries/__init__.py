"""Чтение прогресса и кошелька для публичного API (поток P1)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from gv_core.clock import Clock
from gv_core.errors import InvalidInputError, NotFoundError, RateLimitedError
from progression_service.application.accrual.common import published_config
from progression_service.application.boosts import BoostState, boost_state
from progression_service.application.ports import UnitOfWorkFactory
from progression_service.application.ports.shop import AttemptLimiter
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
    boost: BoostState


@dataclass(frozen=True, slots=True)
class BadgeRefView:
    """Бейдж в публичном профиле."""

    key: str
    title: str
    unlocked_at: datetime


@dataclass(frozen=True, slots=True)
class PublicProgressView:
    """Публичный прогресс игрока."""

    user_id: UUID
    level: int
    total_xp: int
    badges: tuple[BadgeRefView, ...] = ()


@dataclass(frozen=True, slots=True)
class WalletView:
    """Кошелёк: балансы бакетов и последние проводки."""

    earned: int
    paid: int
    recent: tuple[Entry, ...]


@dataclass(frozen=True, slots=True)
class PlayerLevelView:
    """Уровень игрока для internal-пачки (без персональных данных)."""

    user_id: UUID
    level: int
    xp_in_level: int


class TooManyUsersError(InvalidInputError):
    """В пачке уровней больше игроков, чем ``GV_PROGRESSION__LEVELS_MAX_USERS``."""

    default_code = "progression.too_many_users"


class LevelsRateLimitedError(RateLimitedError):
    """Клиент запрашивает уровни слишком часто (``GV_PROGRESSION__LEVELS_PER_MIN``)."""

    default_code = "progression.levels_rate_limited"


LEVELS_WINDOW_S = 60
"""Окно лимита пачек уровней."""


class ProgressQueries:
    """Чтение прогресса и кошелька."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        *,
        recent_limit: int,
        limiter: AttemptLimiter | None = None,
        levels_max_users: int = 50,
        levels_per_min: int = 600,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._recent_limit = recent_limit
        self._limiter = limiter
        self._levels_max_users = levels_max_users
        self._levels_per_min = levels_per_min

    async def levels(self, caller: str, user_ids: Sequence[UUID]) -> list[PlayerLevelView]:
        """Уровни игроков пачкой (internal, сервисный токен ``bff_web``).

        Дубли схлопываются (порядок первого вхождения). Скрытый профиль пропускается; игрок
        без строки прогресса — уровень 1 (как в ``my_progress``). Журналы не читаются.

        Raises:
            TooManyUsersError: уникальных игроков больше ``levels_max_users`` (422).
            LevelsRateLimitedError: больше ``levels_per_min`` пачек в минуту от клиента.
            DependencyUnavailableError: лимитер недоступен (fail-closed).
            ConfigUnavailableError: нет опубликованного конфига.
        """
        unique = list(dict.fromkeys(user_ids))
        if len(unique) > self._levels_max_users:
            raise TooManyUsersError(
                "слишком много игроков в запросе",
                details={"given": len(unique), "max": self._levels_max_users},
            )
        if self._limiter is not None:
            window = await self._limiter.hit(f"levels:{caller}", LEVELS_WINDOW_S)
            if window.count > self._levels_per_min:
                raise LevelsRateLimitedError(
                    "слишком частые запросы уровней", details={"retry_after": window.resets_in_s}
                )
        async with self._uow() as uow:
            cfg = (await published_config(uow)).config
            rows = {r.user_id: r for r in await uow.progress.many(unique)}
        result: list[PlayerLevelView] = []
        for user_id in unique:
            row = rows.get(user_id)
            if row is not None and row.hidden:
                continue
            pos = levels.progress(
                cfg.levels, row.total_xp if row else 0, floor_level=row.level if row else 1
            )
            result.append(PlayerLevelView(user_id, pos.level, pos.xp_in_level))
        return result

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
                boost=await boost_state(uow, user_id),
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
            unlocked = await uow.badges.unlocked(user_id)
            published = await uow.configs.published()
        titles = {} if published is None else {b.key: b.title for b in published.config.badges}
        return PublicProgressView(
            user_id=user_id,
            level=row.level,
            total_xp=row.total_xp,
            badges=tuple(
                BadgeRefView(key, titles.get(key, key), at)
                for key, at in sorted(unlocked.items(), key=lambda kv: (kv[1], kv[0]))
            ),
        )
