"""Наборы дня (D6): планирование вперёд, блокировка в начале дня, курация админом.

Розыгрыш делает Source ядра (``draw`` с seed ``HMAC(secret, mode_key|day)``), game
передаёт контекст: кандидатов из проекции, недавние единицы режима для
``exclude_recent_days``. Все операции идемпотентны: набор на день один (PK), повтор
планировщика ничего не меняет, блокировка повторно — no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import structlog
from prometheus_client import Counter

from game_service.application.catalog import ModeCatalog
from game_service.application.commands.modes import AdminContext
from game_service.application.ports import AdminAction, GameUnitOfWork, Json, UnitOfWorkFactory
from game_service.application.rules import ModeRules, ScheduledSource, is_pool_too_small
from game_service.domain.daily import (
    DailySet,
    DailySetStatus,
    GeneratedBy,
    daily_seed,
    day_in_zone,
)
from game_service.domain.errors import (
    DailyLockedError,
    DailyNotReadyError,
    DailySetNotFoundError,
    ModeNotFoundError,
)
from game_service.domain.projection import CatalogItem
from gv_core.clock import Clock
from gv_core.errors import AppError, InvalidInputError
from gv_db.idempotency import request_fingerprint, run_idempotent
from gv_game_kernel import DrawContext

log = structlog.get_logger(__name__)

DAILY_SETS_FAILED = Counter(
    "game_daily_sets_failed_total", "Наборы дня, которые не удалось разыграть", ["mode_key"]
)
DAILY_ITEMS_AUTO_REPLACED = Counter(
    "game_daily_items_auto_replaced_total", "Единицы набора, заменённые перед блокировкой"
)


@dataclass(frozen=True, slots=True)
class DailySettings:
    """Параметры наборов дня."""

    seed_secret: bytes | None
    candidate_limit: int
    admin_range_days: int


class DailySetGenerator:
    """Розыгрыш единиц набора дня через Source режима."""

    def __init__(self, settings: DailySettings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        """Секрет seed задан (без него наборы не создаются)."""
        return self._settings.seed_secret is not None

    def _schedule(self, rules: ModeRules) -> ScheduledSource:
        schedule = rules.schedule
        if schedule is None:
            raise ModeNotFoundError("режим без наборов дня")
        return schedule

    async def draw(
        self,
        uow: GameUnitOfWork,
        rules: ModeRules,
        day: date,
        *,
        generation: int,
        n: int | None = None,
        exclude: frozenset[UUID] = frozenset(),
    ) -> list[UUID]:
        """Разыграть единицы; пустой список — пул меньше нужного (набор ``failed``)."""
        secret = self._settings.seed_secret
        if secret is None:
            raise DailyNotReadyError("секрет наборов дня не задан (GV_GAME__DAILY_SEED_SECRET)")
        schedule = self._schedule(rules)
        count = schedule.size if n is None else n
        recent = await uow.daily_sets.items_between(
            rules.key, day - timedelta(days=schedule.exclude_recent_days), day - timedelta(days=1)
        )
        ctx = DrawContext(
            mode_key=rules.key,
            domain=rules.domain,
            clue_type=rules.clue_type,
            params_hash=rules.params_hash,
            seed=daily_seed(secret, rules.key, day, generation),
            candidates=uow.candidates,
            day=day,
            exclude_ids=frozenset(recent) | exclude,
        )
        try:
            items = await rules.draw(ctx, count)
        except AppError as exc:
            if not is_pool_too_small(exc):
                raise
            items = []
        if len(items) < count:
            DAILY_SETS_FAILED.labels(rules.key).inc()
            log.warning("game.daily_pool_too_small", mode_key=rules.key, day=day.isoformat())
            return []
        return [item.id for item in items]


async def playable_ids(uow: GameUnitOfWork, rules: ModeRules, ids: list[UUID]) -> set[UUID]:
    """Единицы, играбельные под режим: активны и есть готовые ассеты (D7)."""
    ready = await uow.projection.ready_assets(ids, rules.clue_type, rules.params_hash)
    return set(ready)


class ScheduleDailySets:
    """Периодическая задача: наборы на сегодня и ``schedule_ahead_days`` вперёд, блокировка
    наступивших дней с автоматической заменой непригодных единиц.

    Args:
        uow: Фабрика транзакций.
        clock: Часы.
        catalog: Каталог режимов.
        generator: Розыгрыш.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        catalog: ModeCatalog,
        generator: DailySetGenerator,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._catalog = catalog
        self._generator = generator

    async def execute(self) -> None:
        """Один проход по всем дейликам."""
        if not self._generator.enabled:
            log.warning("game.daily_seed_secret_missing")
            return
        for rules in await self._catalog.published():
            schedule = rules.schedule
            if schedule is None:
                continue
            today = day_in_zone(self._clock.now(), schedule.timezone)
            for offset in range(schedule.schedule_ahead_days + 1):
                await self.ensure(rules, today + timedelta(days=offset), GeneratedBy.SCHEDULE)
            await self.lock_due(rules, today)

    async def ensure(self, rules: ModeRules, day: date, generated_by: GeneratedBy) -> DailySet:
        """Набор на день: создать, если нет; повторить розыгрыш ``failed``."""
        async with self._uow() as uow:
            daily = await uow.daily_sets.get(rules.key, day, for_update=True)
            if daily is not None and daily.status is not DailySetStatus.FAILED:
                return daily
            if daily is None:
                items = await self._generator.draw(uow, rules, day, generation=1)
                daily = DailySet.generated(
                    rules.key,
                    day,
                    mode_version=rules.version,
                    items=items,
                    generated_by=generated_by,
                )
                if not await uow.daily_sets.add(daily):
                    # Набор вставила другая реплика или первый запрос дня — берём её.
                    await uow.rollback()
                    return await self._get(rules.key, day)
            else:
                items = await self._generator.draw(uow, rules, day, generation=daily.generation + 1)
                if not items:
                    return daily
                daily.reroll(items, mode_version=rules.version, generated_by=generated_by)
                await uow.daily_sets.save(daily)
            await uow.commit()
        log.info(
            "game.daily_set_ready",
            mode_key=rules.key,
            day=day.isoformat(),
            status=str(daily.status),
        )
        return daily

    async def _get(self, key: str, day: date) -> DailySet:
        async with self._uow() as uow:
            daily = await uow.daily_sets.get(key, day)
        if daily is None:
            raise DailyNotReadyError("набор дня не создан")
        return daily

    async def lock_due(self, rules: ModeRules, today: date) -> None:
        """Заблокировать наступившие наборы; непригодные единицы заменить перед блокировкой."""
        async with self._uow() as uow:
            for daily in await uow.daily_sets.due_for_lock(rules.key, today):
                set_rules = (
                    rules
                    if daily.mode_version == rules.version
                    else await self._catalog.rules(rules.key, daily.mode_version)
                )
                await self._replace_unplayable(uow, set_rules, daily)
                daily.lock(self._clock.now())
                await uow.daily_sets.save(daily)
            await uow.commit()

    async def _replace_unplayable(
        self, uow: GameUnitOfWork, rules: ModeRules, daily: DailySet
    ) -> None:
        ok = await playable_ids(uow, rules, daily.items)
        bad = [i for i, item in enumerate(daily.items) if item not in ok]
        if not bad:
            return
        fresh = await self._generator.draw(
            uow,
            rules,
            daily.day,
            generation=daily.generation + 100,
            n=len(bad),
            exclude=frozenset(daily.items),
        )
        for index, item in zip(bad, fresh, strict=False):
            daily.auto_replace(index, item)
            DAILY_ITEMS_AUTO_REPLACED.inc()
        log.info(
            "game.daily_items_replaced",
            mode_key=daily.mode_key,
            day=daily.day.isoformat(),
            replaced=len(fresh),
            unplayable=len(bad),
        )


def _item_json(index: int, item_id: UUID, item: CatalogItem | None, *, playable: bool) -> Json:
    return {
        "index": index,
        "media_item_id": str(item_id),
        "title": item.title if item else "",
        "year": item.year if item else None,
        "playable": playable,
    }


class DailySetAdmin:
    """Админка наборов дня: просмотр, замена единицы, перегенерация, поиск кандидатов."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        catalog: ModeCatalog,
        generator: DailySetGenerator,
        settings: DailySettings,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._catalog = catalog
        self._generator = generator
        self._settings = settings

    async def _today(self, key: str) -> tuple[ModeRules, date]:
        _, rules = await self._catalog.latest(key)
        schedule = rules.schedule
        if schedule is None:
            raise ModeNotFoundError("режим без наборов дня")
        return rules, day_in_zone(self._clock.now(), schedule.timezone)

    async def _set_json(self, uow: GameUnitOfWork, daily: DailySet) -> Json:
        rules = await self._catalog.rules(daily.mode_key, daily.mode_version)
        items = await uow.projection.get_items(daily.items)
        ok = await playable_ids(uow, rules, daily.items)
        return {
            "mode_key": daily.mode_key,
            "day": daily.day.isoformat(),
            "mode_version": daily.mode_version,
            "status": str(daily.status),
            "items": [
                _item_json(i, item_id, items.get(item_id), playable=item_id in ok)
                for i, item_id in enumerate(daily.items)
            ],
        }

    async def list_sets(self, key: str, day_from: date, day_to: date) -> list[Json]:
        """Наборы за период.

        Raises:
            InvalidInputError: период пустой или длиннее ``admin_range_days``.
        """
        if day_to < day_from or (day_to - day_from).days >= self._settings.admin_range_days:
            raise InvalidInputError(
                "некорректный период",
                code="game.daily_range_invalid",
                details={"max_days": self._settings.admin_range_days},
            )
        async with self._uow() as uow:
            sets = await uow.daily_sets.list_range(key, day_from, day_to)
            return [await self._set_json(uow, s) for s in sets]

    async def _audit(
        self, uow: GameUnitOfWork, ctx: AdminContext, action: str, target: str, **details: Any
    ) -> None:
        await uow.audit.record(
            AdminAction(
                actor_id=ctx.actor_id,
                action=action,
                target=target,
                reason=ctx.reason,
                at=self._clock.now(),
                details=details,
            )
        )

    async def replace(
        self, key: str, day: date, index: int, item_id: UUID, *, ctx: AdminContext
    ) -> Json:
        """Заменить единицу набора (``scheduled``, до начала дня); аудит замены."""
        _, today = await self._today(key)
        fingerprint = request_fingerprint(
            "daily_set.replace",
            f"{key}|{day}",
            {"index": index, "item": str(item_id), "reason": ctx.reason},
        )
        async with self._uow() as uow:

            async def perform() -> Json:
                daily = await uow.daily_sets.get(key, day, for_update=True)
                if daily is None:
                    raise DailySetNotFoundError("набора дня нет")
                if daily.is_locked_on(today):
                    raise DailyLockedError("день начался — набор только для чтения")
                rules = await self._catalog.rules(key, daily.mode_version)
                playable = item_id in await playable_ids(uow, rules, [item_id])
                previous = daily.replace_item(index, item_id, playable=playable, today=today)
                await uow.daily_sets.save(daily)
                await self._audit(
                    uow,
                    ctx,
                    "daily_set.replace_item",
                    f"{key}|{day.isoformat()}",
                    index=index,
                    previous=str(previous),
                    media_item_id=str(item_id),
                )
                return await self._set_json(uow, daily)

            response, _ = await run_idempotent(
                uow.idempotency, ctx.actor_id, ctx.idempotency_key, fingerprint, perform
            )
            await uow.commit()
        return response

    async def regenerate(self, key: str, day: date, *, ctx: AdminContext) -> Json:
        """Перегенерировать набор (``scheduled``/``failed``, до начала дня)."""
        rules, today = await self._today(key)
        fingerprint = request_fingerprint(
            "daily_set.regenerate", f"{key}|{day}", {"reason": ctx.reason}
        )
        async with self._uow() as uow:

            async def perform() -> Json:
                daily = await uow.daily_sets.get(key, day, for_update=True)
                if daily is None:
                    raise DailySetNotFoundError("набора дня нет")
                if daily.is_locked_on(today):
                    raise DailyLockedError("день начался — набор только для чтения")
                items = await self._generator.draw(uow, rules, day, generation=daily.generation + 1)
                daily.regenerate(
                    items, mode_version=rules.version, generated_by=GeneratedBy.ADMIN, today=today
                )
                await uow.daily_sets.save(daily)
                await self._audit(
                    uow,
                    ctx,
                    "daily_set.regenerate",
                    f"{key}|{day.isoformat()}",
                    generation=daily.generation,
                    status=str(daily.status),
                )
                return await self._set_json(uow, daily)

            response, _ = await run_idempotent(
                uow.idempotency, ctx.actor_id, ctx.idempotency_key, fingerprint, perform
            )
            await uow.commit()
        return response

    async def search(
        self, *, q: str | None, domain: str | None, playable_for_mode: str | None, limit: int
    ) -> list[Json]:
        """Кандидаты на замену с признаком играбельности под режим."""
        rules = None
        if playable_for_mode is not None:
            _, rules = await self._catalog.latest(playable_for_mode)
            domain = domain or rules.domain
        async with self._uow() as uow:
            items = await uow.projection.search(q=q, domain=domain, limit=limit)
            ok = (
                await playable_ids(uow, rules, [i.media_item_id for i in items])
                if rules is not None
                else set()
            )
        return [
            {
                "media_item_id": str(i.media_item_id),
                "domain": i.domain,
                "kind": i.kind,
                "title": i.title,
                "year": i.year,
                "status": str(i.status),
                "playable": (i.media_item_id in ok) if rules is not None else None,
            }
            for i in items
        ]
