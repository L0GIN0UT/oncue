"""Колесо: просмотр и спин раз в сутки (ADR 0044; D8, R1).

Спин идемпотентен по ``(user_id, день)``: сутки считает зона конфига, запись ``wheel_spins``
защищена PK и ``INSERT … ON CONFLICT DO NOTHING``. Сектор выбирает сервер через порт ``Random``
по весам действующей версии конфига. Приз выдаётся в той же транзакции, что и запись спина.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import ConflictError
from gv_core.ids import uuid7
from gv_core.random import Random
from progression_service.application.accrual.common import published_config
from progression_service.application.accrual.posting import Poster
from progression_service.application.ports import (
    ProgressionUnitOfWork,
    PublishedConfig,
    UnitOfWorkFactory,
)
from progression_service.application.ports.rewards import SpinRecord
from progression_service.application.quests.tracker import ProgressTracker
from progression_service.domain import boosts, metrics, wheel
from progression_service.domain.boosts import Boost
from progression_service.domain.config import PrizeDef, PrizeKind, SectorDef
from progression_service.domain.events import PrizePayload, WheelSpun
from progression_service.domain.inventory import InventoryItem, Source
from progression_service.domain.metrics import MetricUpdate

log = structlog.get_logger(__name__)

WHEEL_DISABLED = "progression.wheel_disabled"
WHEEL_REASON = "wheel"
NO_PRIZE = PrizeDef(kind=PrizeKind.NONE, amount=0, multiplier=None, matches=None, item_key=None)


class WheelDisabledError(ConflictError):
    """Колесо выключено в конфиге (409)."""

    default_code = WHEEL_DISABLED

    def __init__(self) -> None:
        super().__init__("колесо сейчас выключено", code=WHEEL_DISABLED)


@dataclass(frozen=True, slots=True)
class SectorChance:
    """Сектор колеса с шансом."""

    sector: SectorDef
    chance: float


@dataclass(frozen=True, slots=True)
class SpinOutcome:
    """Спин за сутки; ``repeated`` - запись за эти сутки уже была."""

    record: SpinRecord
    repeated: bool


@dataclass(frozen=True, slots=True)
class WheelState:
    """Колесо для игрока: секторы с шансами и состояние на сегодня."""

    enabled: bool
    sectors: tuple[SectorChance, ...]
    day: date
    today: SpinRecord | None
    next_spin_at: datetime | None
    config_version: int


class WheelService:
    """Просмотр колеса и спин."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, random: Callable[[], Random]) -> None:
        self._uow = uow
        self._clock = clock
        self._random = random

    async def view(self, user_id: UUID) -> WheelState:
        """Колесо и спин игрока за сегодня.

        Raises:
            ConfigUnavailableError: нет опубликованного конфига.
        """
        async with self._uow() as uow:
            published = await published_config(uow)
            cfg = published.config
            day = wheel.wheel_day(self._clock.now(), cfg.tz)
            today = await uow.spins.get(user_id, day)
        sectors = cfg.wheel.sectors
        return WheelState(
            enabled=cfg.wheel.enabled,
            sectors=tuple(
                SectorChance(s, c) for s, c in zip(sectors, wheel.chances(sectors), strict=True)
            ),
            day=day,
            today=today,
            next_spin_at=None if today is None else wheel.next_spin_at(day, cfg.tz),
            config_version=published.version,
        )

    async def spin(self, user_id: UUID) -> SpinOutcome:
        """Крутить колесо; повтор за те же сутки возвращает ту же запись.

        Raises:
            WheelDisabledError: колесо выключено (409).
            ConfigUnavailableError: нет опубликованного конфига.
        """
        async with self._uow() as uow:
            published = await published_config(uow)
            cfg = published.config
            if not cfg.wheel.enabled:
                raise WheelDisabledError
            now = self._clock.now()
            day = wheel.wheel_day(now, cfg.tz)
            poster = Poster(uow, published, now)
            # Блокировка игрока сериализует его спины и матчи; ON CONFLICT ниже - страховка.
            await poster.lock_user(user_id)
            existing = await uow.spins.get(user_id, day)
            if existing is not None:
                return SpinOutcome(existing, repeated=True)
            sector = wheel.pick_sector(cfg.wheel.sectors, self._random())
            prize = await self._effective_prize(uow, published, user_id, sector.prize)
            record = SpinRecord(user_id, day, sector.key, prize, published.version, now)
            if not await uow.spins.add(record):
                stored = await uow.spins.get(user_id, day)
                if stored is None:  # pragma: no cover - ON CONFLICT без строки невозможен
                    raise RuntimeError("спин не записан и не найден")
                return SpinOutcome(stored, repeated=True)
            await self._grant(uow, poster, record)
            await self._track_streak(uow, user_id, day, now)
            uow.publish(
                WheelSpun(
                    aggregate_id=str(user_id),
                    user_id=user_id,
                    day=day.isoformat(),
                    sector_key=sector.key,
                    prize=_payload(prize),
                    config_version=published.version,
                    at=now,
                )
            )
            await uow.commit()
        log.info("progression.wheel_spun", sector=sector.key, prize=prize.kind)
        return SpinOutcome(record, repeated=False)

    async def _track_streak(
        self, uow: ProgressionUnitOfWork, user_id: UUID, day: date, now: datetime
    ) -> None:
        """Серия суток со спином - уровень метрики ``wheel_spin_streak`` (в транзакции спина)."""

        async def build(_: PublishedConfig, tx: ProgressionUnitOfWork) -> list[MetricUpdate]:
            since = day - timedelta(days=wheel.STREAK_LOOKBACK_DAYS)
            streak = wheel.spin_streak(await tx.spins.days(user_id, since, day), day)
            return [MetricUpdate(metrics.WHEEL_SPIN_STREAK, streak)]

        await ProgressTracker(self._uow, self._clock).apply(
            uow, f"wheel:{user_id}:{day.isoformat()}", user_id, now, build
        )

    @staticmethod
    async def _effective_prize(
        uow: ProgressionUnitOfWork, published: PublishedConfig, user_id: UUID, prize: PrizeDef
    ) -> PrizeDef:
        """Приз с учётом очереди бустов: при полной очереди буст не выдаётся (приз пустой)."""
        if prize.kind is not PrizeKind.BOOST:
            return prize
        live = await uow.boosts.live(user_id, lock=True)
        if boosts.can_enqueue(live, published.config.wheel.max_boost_queue):
            return prize
        return NO_PRIZE

    @staticmethod
    async def _grant(uow: ProgressionUnitOfWork, poster: Poster, record: SpinRecord) -> None:
        """Выдать приз в транзакции спина."""
        grant = _GRANTS.get(record.prize.kind)
        if grant is not None:  # NONE ничего не выдаёт
            await grant(uow, poster, record)


def _ref(record: SpinRecord) -> tuple[str, str]:
    """Сутки спина и идентификатор операции ``wheel:{user}:{day}``."""
    day = record.day.isoformat()
    return day, f"wheel:{record.user_id}:{day}"


async def _grant_verses(
    uow: ProgressionUnitOfWork,  # noqa: ARG001 - единая сигнатура выдачи приза
    poster: Poster,
    record: SpinRecord,
) -> None:
    day, op = _ref(record)
    await poster.verses(
        record.user_id, op_id=op, amount=record.prize.amount, reason=WHEEL_REASON, source_ref=day
    )


async def _grant_xp(
    uow: ProgressionUnitOfWork,  # noqa: ARG001 - единая сигнатура выдачи приза
    poster: Poster,
    record: SpinRecord,
) -> None:
    day, op = _ref(record)
    await poster.xp(
        record.user_id,
        op_id=f"{op}:xp",
        base_amount=record.prize.amount,
        reason=WHEEL_REASON,
        source_ref=day,
    )


async def _grant_boost(
    uow: ProgressionUnitOfWork,
    poster: Poster,  # noqa: ARG001
    record: SpinRecord,
) -> None:
    prize = record.prize
    assert prize.multiplier is not None  # noqa: S101 - инвариант конфига и схемы
    assert prize.matches is not None  # noqa: S101
    boost = Boost(uuid7(), record.user_id, prize.multiplier, prize.matches, record.spun_at)
    await uow.boosts.add(boost, source=WHEEL_REASON, source_ref=_ref(record)[1])


async def _grant_item(
    uow: ProgressionUnitOfWork,
    poster: Poster,
    record: SpinRecord,
) -> None:
    """Предмет - в инвентарь (``source = wheel``); дубль - Версы по редкости (ADR 0045).

    Предмета с таким ключом нет в таблице ``items`` (конфиг опередил админку) - спин не
    падает: ошибка в лог, выдача как в 5a - запись журнала ``grants_log``.
    """
    key = record.prize.item_key
    assert key is not None  # noqa: S101
    day, op = _ref(record)
    item = await uow.items.get_by_key(key)
    # Снятый с продажи предмет и предмет события (выдаётся только событием) - как отсутствующие.
    if item is None or not item.active or item.event_key is not None:
        log.error("progression.wheel_item_missing", item_key=key)
        await uow.grants.add(record.user_id, WHEEL_REASON, op, key, record.spun_at)
        return
    entry = InventoryItem(record.user_id, item.id, Source.WHEEL, record.spun_at, None, None)
    if await uow.inventory.add(entry):
        return
    shop = poster.config.shop
    rarity = None if shop is None else shop.rarity(item.rarity)
    if rarity is not None:
        await poster.verses(
            record.user_id,
            op_id=f"{op}:dup",
            amount=rarity.duplicate_verses,
            reason=WHEEL_REASON,
            source_ref=day,
        )


_Grant = Callable[[ProgressionUnitOfWork, Poster, SpinRecord], Awaitable[None]]
_GRANTS: dict[PrizeKind, _Grant] = {
    PrizeKind.VERSES: _grant_verses,
    PrizeKind.XP: _grant_xp,
    PrizeKind.BOOST: _grant_boost,
    PrizeKind.ITEM: _grant_item,
}
"""Выдача приза по виду; новый вид приза - новая функция и строка здесь."""

EVENT_KINDS: dict[PrizeKind, PrizeKind] = {
    kind: (PrizeKind.NONE if kind is PrizeKind.ITEM else kind) for kind in PrizeKind
}
"""Вид приза в событии ``WheelSpun``: схема не знает ``item`` - предмет в событии как ``none``."""


def _payload(prize: PrizeDef) -> PrizePayload:
    """Приз для события ``WheelSpun``."""
    kind = EVENT_KINDS[prize.kind]
    return PrizePayload(
        kind=kind.value,
        amount=0 if kind is PrizeKind.NONE else prize.amount,
        multiplier=None if prize.multiplier is None else float(prize.multiplier),
        matches=prize.matches,
    )
