"""Postgres-репозиторий сезонных событий (5d; ADR 0054-0056)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from progression_service.application.ports.events import (
    ArchiveRow,
    EventRow,
    FindCount,
    ProgressRow,
    RunRow,
)
from progression_service.infrastructure.db.tables_events import (
    event_completions,
    event_find_progress,
    event_runs,
    events,
)

# Произвольная константа ключа advisory-блокировки публикации запусков.
_PUBLISH_LOCK_KEY = 5_100_004
_PUBLISHED = ("published", "active", "finished", "stopped")


def _event(r: Any) -> EventRow:
    return EventRow(
        id=r.id,
        key=r.key,
        body=dict(r.body),
        body_sha256=r.body_sha256,
        map_key=r.map_key,
        created_by=r.created_by,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def _run(r: Any) -> RunRow:
    return RunRow(
        id=r.id,
        event_id=r.event_id,
        event_key=r.event_key,
        seq=r.seq,
        status=r.status,
        starts_at=r.starts_at,
        ends_at=r.ends_at,
        snapshot=None if r.snapshot is None else dict(r.snapshot),
        body_sha256=r.body_sha256,
        published_at=r.published_at,
        stopped_at=r.stopped_at,
        created_by=r.created_by,
        created_at=r.created_at,
    )


def _runs_query() -> Any:
    return select(*event_runs.c, events.c.key.label("event_key")).join(
        events, events.c.id == event_runs.c.event_id
    )


class SqlEvents:
    """События, запуски и прогресс игроков."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ------------------------------------------------------------------ определения
    async def add_event(self, row: EventRow) -> bool:
        """Создать событие; ``False`` - ключ занят."""
        result = (
            await self._s.execute(
                pg_insert(events)
                .values(
                    id=row.id,
                    key=row.key,
                    body=row.body,
                    body_sha256=row.body_sha256,
                    map_key=row.map_key,
                    created_by=row.created_by,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                .on_conflict_do_nothing(index_elements=[events.c.key])
                .returning(events.c.id)
            )
        ).first()
        return result is not None

    async def get_event(self, key: str, *, for_update: bool = False) -> EventRow | None:
        """Событие по ключу."""
        stmt = select(events).where(events.c.key == key)
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._s.execute(stmt)).first()
        return None if row is None else _event(row)

    async def save_event(self, row: EventRow) -> None:
        """Записать тело, хеш, карту и ``updated_at``."""
        await self._s.execute(
            update(events)
            .where(events.c.id == row.id)
            .values(
                body=row.body,
                body_sha256=row.body_sha256,
                map_key=row.map_key,
                updated_at=row.updated_at,
            )
        )

    async def list_events(self) -> list[EventRow]:
        """Все события по ключу."""
        rows = await self._s.execute(select(events).order_by(events.c.key))
        return [_event(r) for r in rows]

    # ------------------------------------------------------------------ запуски
    async def lock_publishing(self) -> None:
        """Advisory-lock транзакции: публикации идут по очереди."""
        await self._s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _PUBLISH_LOCK_KEY})

    async def add_run(self, run: RunRow) -> RunRow:
        """Добавить запуск со следующим ``seq`` события (событие уже заблокировано вызывающим)."""
        top = await self._s.scalar(
            select(func.max(event_runs.c.seq)).where(event_runs.c.event_id == run.event_id)
        )
        seq = (top or 0) + 1
        await self._s.execute(
            event_runs.insert().values(
                id=run.id,
                event_id=run.event_id,
                seq=seq,
                status=run.status,
                starts_at=run.starts_at,
                ends_at=run.ends_at,
                snapshot=run.snapshot,
                body_sha256=run.body_sha256,
                published_at=run.published_at,
                stopped_at=run.stopped_at,
                created_by=run.created_by,
                created_at=run.created_at,
            )
        )
        return _with_seq(run, seq)

    async def get_run(
        self, run_id: UUID, *, for_update: bool = False, for_share: bool = False
    ) -> RunRow | None:
        """Запуск по id (``for_share`` - держит строку от остановки до конца транзакции)."""
        stmt = _runs_query().where(event_runs.c.id == run_id)
        if for_update:
            stmt = stmt.with_for_update(of=event_runs)
        elif for_share:
            stmt = stmt.with_for_update(read=True, of=event_runs)
        row = (await self._s.execute(stmt)).first()
        return None if row is None else _run(row)

    async def save_run(self, run: RunRow) -> None:
        """Записать статус, снимок и метки времени."""
        await self._s.execute(
            update(event_runs)
            .where(event_runs.c.id == run.id)
            .values(
                status=run.status,
                starts_at=run.starts_at,
                ends_at=run.ends_at,
                snapshot=run.snapshot,
                body_sha256=run.body_sha256,
                published_at=run.published_at,
                stopped_at=run.stopped_at,
            )
        )

    async def runs_of(self, event_id: UUID) -> list[RunRow]:
        """Запуски события по ``seq``."""
        rows = await self._s.execute(
            _runs_query().where(event_runs.c.event_id == event_id).order_by(event_runs.c.seq)
        )
        return [_run(r) for r in rows]

    async def latest_runs(self) -> dict[UUID, RunRow]:
        """Последний запуск каждого события."""
        top = (
            select(event_runs.c.event_id, func.max(event_runs.c.seq).label("seq"))
            .group_by(event_runs.c.event_id)
            .subquery()
        )
        rows = await self._s.execute(
            _runs_query().join(
                top, and_(top.c.event_id == event_runs.c.event_id, top.c.seq == event_runs.c.seq)
            )
        )
        return {r.event_id: _run(r) for r in rows}

    async def has_locking_run(self, event_id: UUID, now: datetime) -> bool:
        """Есть запуск ``published/active``, ещё не закончившийся по времени."""
        found = await self._s.scalar(
            select(
                exists().where(
                    event_runs.c.event_id == event_id,
                    event_runs.c.status.in_(("published", "active")),
                    event_runs.c.ends_at > now,
                )
            )
        )
        return bool(found)

    async def overlaps(self, starts_at: datetime, ends_at: datetime, *, exclude: UUID) -> bool:
        """Пересекается ли период с опубликованным запуском (остановленный - до остановки)."""
        end = func.least(
            event_runs.c.ends_at, func.coalesce(event_runs.c.stopped_at, event_runs.c.ends_at)
        )
        found = await self._s.scalar(
            select(
                exists().where(
                    event_runs.c.status.in_(_PUBLISHED),
                    event_runs.c.id != exclude,
                    event_runs.c.starts_at < ends_at,
                    end > starts_at,
                )
            )
        )
        return bool(found)

    async def active_at(self, at: datetime) -> RunRow | None:
        """Запуск, в период которого попадает ``at``."""
        row = (
            await self._s.execute(
                _runs_query()
                .where(
                    event_runs.c.status.in_(_PUBLISHED),
                    event_runs.c.starts_at <= at,
                    event_runs.c.ends_at > at,
                    or_(event_runs.c.stopped_at.is_(None), event_runs.c.stopped_at > at),
                )
                .limit(1)
            )
        ).first()
        return None if row is None else _run(row)

    async def runs_for_key(self, key: str) -> list[RunRow]:
        """Опубликованные запуски события по ключу, новые первыми."""
        rows = await self._s.execute(
            _runs_query()
            .where(events.c.key == key, event_runs.c.status.in_(_PUBLISHED))
            .order_by(event_runs.c.starts_at.desc())
        )
        return [_run(r) for r in rows]

    async def to_activate(self, now: datetime) -> list[RunRow]:
        """Хранимо ``published``, срок начался."""
        rows = await self._s.execute(
            _runs_query()
            .where(event_runs.c.status == "published", event_runs.c.starts_at <= now)
            .order_by(event_runs.c.starts_at)
            .with_for_update(of=event_runs, skip_locked=True)
        )
        return [_run(r) for r in rows]

    async def to_finish(self, now: datetime) -> list[RunRow]:
        """``published/active``, срок вышел."""
        rows = await self._s.execute(
            _runs_query()
            .where(event_runs.c.status.in_(("published", "active")), event_runs.c.ends_at <= now)
            .order_by(event_runs.c.ends_at)
            .with_for_update(of=event_runs, skip_locked=True)
        )
        return [_run(r) for r in rows]

    async def set_status(self, run_id: UUID, status: str, *, expected: tuple[str, ...]) -> bool:
        """Сменить хранимый статус, если он из ``expected``."""
        row = (
            await self._s.execute(
                update(event_runs)
                .where(event_runs.c.id == run_id, event_runs.c.status.in_(expected))
                .values(status=status)
                .returning(event_runs.c.id)
            )
        ).first()
        return row is not None

    # ------------------------------------------------------------------ прогресс игроков
    async def progress_of(self, run_id: UUID, user_id: UUID) -> list[ProgressRow]:
        """Строки прогресса игрока по запуску."""
        rows = await self._s.execute(
            select(event_find_progress).where(
                event_find_progress.c.run_id == run_id, event_find_progress.c.user_id == user_id
            )
        )
        return [ProgressRow(r.find_key, r.period_key, r.progress, r.unlocked_at) for r in rows]

    async def save_progress(  # noqa: PLR0917 - ключ строки + значения
        self,
        run_id: UUID,
        user_id: UUID,
        find_key: str,
        period_key: str,
        progress: int,
        unlocked_at: datetime | None,
        now: datetime,
    ) -> None:
        """Записать прогресс (upsert)."""
        values = {"progress": progress, "unlocked_at": unlocked_at, "updated_at": now}
        await self._s.execute(
            pg_insert(event_find_progress)
            .values(
                run_id=run_id, user_id=user_id, find_key=find_key, period_key=period_key, **values
            )
            .on_conflict_do_update(
                index_elements=[
                    event_find_progress.c.run_id,
                    event_find_progress.c.user_id,
                    event_find_progress.c.find_key,
                    event_find_progress.c.period_key,
                ],
                set_=values,
            )
        )

    async def add_completion(self, run_id: UUID, user_id: UUID, now: datetime) -> bool:
        """Закрыть событие игроку; ``False`` - уже закрыто."""
        row = (
            await self._s.execute(
                pg_insert(event_completions)
                .values(run_id=run_id, user_id=user_id, completed_at=now)
                .on_conflict_do_nothing()
                .returning(event_completions.c.run_id)
            )
        ).first()
        return row is not None

    async def is_completed(self, run_id: UUID, user_id: UUID) -> bool:
        """Закрыто ли событие игроком."""
        found = await self._s.scalar(
            select(
                exists().where(
                    event_completions.c.run_id == run_id, event_completions.c.user_id == user_id
                )
            )
        )
        return bool(found)

    async def archive(self, user_id: UUID, limit: int) -> list[ArchiveRow]:
        """Запуски, где у игрока есть прогресс, новые первыми."""
        mine = event_find_progress.c.user_id == user_id
        found = (
            select(
                event_find_progress.c.run_id.label("run_id"),
                func.count(event_find_progress.c.find_key)
                .filter(event_find_progress.c.unlocked_at.is_not(None))
                .label("found"),
            )
            .where(mine)
            .group_by(event_find_progress.c.run_id)
            .subquery()
        )
        done = exists().where(
            event_completions.c.run_id == event_runs.c.id, event_completions.c.user_id == user_id
        )
        rows = await self._s.execute(
            _runs_query()
            .add_columns(found.c.found, done.label("completed"))
            .join(found, found.c.run_id == event_runs.c.id)
            .order_by(event_runs.c.starts_at.desc())
            .limit(limit)
        )
        return [ArchiveRow(_run(r), int(r.found), bool(r.completed)) for r in rows]

    async def stats(self, run_id: UUID) -> tuple[int, int, list[FindCount]]:
        """Игроков с прогрессом, закрывших событие, открывших по находкам."""
        players = await self._s.scalar(
            select(func.count(func.distinct(event_find_progress.c.user_id))).where(
                event_find_progress.c.run_id == run_id
            )
        )
        completed = await self._s.scalar(
            select(func.count()).where(event_completions.c.run_id == run_id)
        )
        rows = await self._s.execute(
            select(event_find_progress.c.find_key, func.count())
            .where(
                event_find_progress.c.run_id == run_id,
                event_find_progress.c.unlocked_at.is_not(None),
            )
            .group_by(event_find_progress.c.find_key)
        )
        return int(players or 0), int(completed or 0), [FindCount(k, int(n)) for k, n in rows]


def _with_seq(run: RunRow, seq: int) -> RunRow:
    return RunRow(
        id=run.id,
        event_id=run.event_id,
        event_key=run.event_key,
        seq=seq,
        status=run.status,
        starts_at=run.starts_at,
        ends_at=run.ends_at,
        snapshot=run.snapshot,
        body_sha256=run.body_sha256,
        published_at=run.published_at,
        stopped_at=run.stopped_at,
        created_by=run.created_by,
        created_at=run.created_at,
    )
