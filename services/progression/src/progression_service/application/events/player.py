"""События для игрока: баннер, страница, архив, claim спрятанного элемента (ADR 0054, 0055)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from progression_service.application.accrual.common import published_config
from progression_service.application.accrual.posting import Poster
from progression_service.application.events.unlock import PlayerRun, unlock_find
from progression_service.application.ports import ProgressionUnitOfWork, UnitOfWorkFactory
from progression_service.application.ports.events import RunRow
from progression_service.application.ports.shop import AttemptLimiter
from progression_service.domain.events_season import (
    SERVER,
    UI_SPOT,
    Definition,
    EventFindLockedError,
    EventFindNotFoundError,
    EventFindNotUiSpotError,
    EventNotActiveError,
    EventNotFoundError,
    FindDef,
    FindState,
    RunStatus,
    find_state,
    others_done,
    parse_definition,
    run_status,
)

from gv_core.clock import Clock
from gv_core.errors import RateLimitedError

type Json = dict[str, Any]
PublicUrl = Callable[[str], str]

ARCHIVE_LIMIT = 50


class EventClaimRateLimitedError(RateLimitedError):
    """Слишком много кликов по спрятанным элементам за окно (429, ``Retry-After``)."""

    default_code = "progression.event_claim_rate_limited"

    def __init__(self, retry_after_s: int) -> None:
        super().__init__("слишком много попыток, подождите", details={"retry_after": retry_after_s})


def _iso(value: datetime) -> str:
    return value.isoformat()


def _definition(run: RunRow) -> Definition:
    assert run.snapshot is not None  # noqa: S101 - опубликованный запуск всегда со снимком
    return parse_definition(run.snapshot)


async def _tz(uow: ProgressionUnitOfWork) -> ZoneInfo:
    published = await uow.configs.published()
    return ZoneInfo("UTC") if published is None else published.config.tz


def _find_json(
    find: FindDef,
    state: FindState,
    player: PlayerRun,
    day: date,
) -> Json:
    trigger = find.trigger
    progress: int | None = None
    if trigger.type == SERVER and state is not FindState.LOCKED:
        progress = (
            trigger.target
            if state is FindState.FOUND
            else player.progress_of(find.key, trigger.period_key(day))
        )
    unlocked_at = [
        row.unlocked_at
        for (key, _), row in player.rows.items()
        if key == find.key and row.unlocked_at is not None
    ]
    found_at = _iso(max(unlocked_at)) if unlocked_at else None
    return {
        "key": find.key,
        "number": find.number,
        "title": find.title,
        "x_pct": find.x_pct,
        "y_pct": find.y_pct,
        "final": find.final,
        "state": state.value,
        "diary": find.diary_after if state is FindState.FOUND else find.diary_before,
        "reward": {"xp": find.xp, "verses": find.verses},
        "play_page": find.play_page,
        "trigger": {
            "type": trigger.type,
            "page_key": trigger.page_key,
            "metric": trigger.metric,
            "progress": progress,
            "target": trigger.target,
            "period": trigger.period,
        },
        "found_at": found_at,
    }


class EventPlayerService:
    """Чтение событий игрока и засчёт находок-элементов интерфейса."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        public_url: PublicUrl,
        *,
        limiter: AttemptLimiter,
        claim_limit: int,
        claim_window_s: int,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._public_url = public_url
        self._limiter = limiter
        self._claim_limit = claim_limit
        self._claim_window_s = claim_window_s

    async def active(self) -> Json:
        """Баннер активного запуска или ``{"event": None}`` (анонимно)."""
        async with self._uow() as uow:
            run = await uow.events.active_at(self._clock.now())
            if run is None or run.snapshot is None:
                return {"event": None}
            definition = _definition(run)
        return {
            "event": {
                "run_id": str(run.id),
                "key": run.event_key,
                "title": definition.title,
                "theme_key": definition.theme_key,
                "starts_at": _iso(run.starts_at),
                "ends_at": _iso(run.ends_at),
            }
        }

    async def page(self, user_id: UUID, key: str) -> Json:
        """Страница события: активный запуск, иначе последний завершённый или остановленный.

        Raises:
            EventNotFoundError: нет события или у него нет видимого запуска (404).
        """
        async with self._uow() as uow:
            now = self._clock.now()
            run = await self._visible_run(uow, key, now)
            event = await uow.events.get_event(key)
            definition = _definition(run)
            player = await PlayerRun.load(uow, run, definition, user_id)
            completed = await uow.events.is_completed(run.id, user_id)
            day = now.astimezone(await _tz(uow)).date()
            reward_item = await self._reward_item(uow, definition)
        finds = [
            _find_json(f, find_state(f, player.unlocked, definition), player, day)
            for f in definition.finds
        ]
        status = run_status(run.status, run.starts_at, run.ends_at, now)
        map_key = None if event is None else event.map_key
        return {
            "key": key,
            "title": definition.title,
            "description": definition.description,
            "theme_key": definition.theme_key,
            "status": status.value,
            "starts_at": _iso(run.starts_at),
            "ends_at": _iso(run.ends_at),
            "map_url": None if map_key is None else self._public_url(map_key),
            "found": len(player.unlocked),
            "total": definition.total,
            "completed": completed,
            "reward_item": reward_item,
            "finds": finds,
        }

    async def mine(self, user_id: UUID) -> Json:
        """Архив: запуски, где у игрока есть прогресс (новые первыми, не более 50)."""
        async with self._uow() as uow:
            now = self._clock.now()
            rows = await uow.events.archive(user_id, ARCHIVE_LIMIT)
        items = []
        for row in rows:
            run = row.run
            if run.snapshot is None:
                continue
            definition = _definition(run)
            items.append(
                {
                    "key": run.event_key,
                    "title": definition.title,
                    "status": run_status(run.status, run.starts_at, run.ends_at, now).value,
                    "starts_at": _iso(run.starts_at),
                    "ends_at": _iso(run.ends_at),
                    "found": row.found,
                    "total": definition.total,
                    "completed": row.completed,
                }
            )
        return {"items": items}

    async def claim(self, user_id: UUID, key: str, find_key: str) -> Json:
        """Засчитать находку-элемент интерфейса; повтор возвращает прежний итог.

        Raises:
            EventClaimRateLimitedError: лимит запросов игрока (429).
            DependencyUnavailableError: лимитер недоступен (503, fail-closed).
            EventNotFoundError, EventFindNotFoundError: нет события или находки (404).
            EventNotActiveError, EventFindNotUiSpotError, EventFindLockedError: 409.
        """
        window = await self._limiter.hit(f"event_claim:{user_id}", self._claim_window_s)
        if window.count > self._claim_limit:
            raise EventClaimRateLimitedError(window.resets_in_s)
        async with self._uow() as uow:
            now = self._clock.now()
            run = await uow.events.active_at(now)
            if run is None or run.event_key != key or run.snapshot is None:
                # Как страница: событие без начавшегося запуска для игрока не существует.
                started = (
                    run_status(r.status, r.starts_at, r.ends_at, now)
                    for r in await uow.events.runs_for_key(key)
                    if r.snapshot is not None
                )
                if any(s is not RunStatus.PUBLISHED for s in started):
                    raise EventNotActiveError
                raise EventNotFoundError
            definition = _definition(run)
            find = definition.find(find_key)
            if find is None:
                raise EventFindNotFoundError
            if find.trigger.type != UI_SPOT:
                raise EventFindNotUiSpotError
            published = await published_config(uow)
            poster = Poster(uow, published, now)
            await poster.lock_user(user_id)  # сериализует клики одного игрока
            run = await self._still_active(uow, run, now)
            player = await PlayerRun.load(uow, run, definition, user_id)
            newly = False
            if find.key not in player.unlocked:
                if find.final and not others_done(definition, player.unlocked):
                    raise EventFindLockedError
                newly = await unlock_find(uow, poster, player, find, "event", 1, now)
            completed = await uow.events.is_completed(run.id, user_id)
            await uow.commit()
            day = now.astimezone(await _tz(uow)).date()
        state = find_state(find, player.unlocked, definition)
        return {
            "find": _find_json(find, state, player, day),
            "found": len(player.unlocked),
            "total": definition.total,
            "completed": completed,
            "newly_unlocked": newly,
        }

    @staticmethod
    async def _still_active(uow: ProgressionUnitOfWork, run: RunRow, now: datetime) -> RunRow:
        """Перечитать запуск под ``FOR SHARE``: остановка после блокировки игрока не пройдёт."""
        fresh = await uow.events.get_run(run.id, for_share=True)
        if fresh is None or run_status(fresh.status, fresh.starts_at, fresh.ends_at, now) is not (
            RunStatus.ACTIVE
        ):
            raise EventNotActiveError
        return fresh

    @staticmethod
    async def _visible_run(uow: ProgressionUnitOfWork, key: str, now: datetime) -> RunRow:
        runs = await uow.events.runs_for_key(key)
        statuses = [(r, run_status(r.status, r.starts_at, r.ends_at, now)) for r in runs]
        for run, status in statuses:
            if status is RunStatus.ACTIVE and run.snapshot is not None:
                return run
        for run, status in statuses:  # новые первыми
            if status in (RunStatus.FINISHED, RunStatus.STOPPED) and run.snapshot is not None:
                return run
        raise EventNotFoundError

    async def _reward_item(self, uow: ProgressionUnitOfWork, definition: Definition) -> Json | None:
        if definition.item_key is None:
            return None
        item = await uow.items.get_by_key(definition.item_key)
        if item is None or item.asset_key is None:
            return None
        return {
            "item_key": item.key,
            "title": item.title,
            "image_url": self._public_url(item.poster_key or item.asset_key),
        }
