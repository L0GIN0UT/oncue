"""Postgres rating: репозитории и Unit of Work с записью событий в outbox.

Два режима UoW: своя транзакция (API, задачи) или SAVEPOINT в транзакции consumer, где уже
стоит отметка inbox (коммит всей транзакции делает ``EventProcessor``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import date, datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from sqlalchemy import Select, and_, exists, func, insert, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from gv_core.clock import Clock
from gv_core.domain import AggregateRoot
from gv_core.ids import uuid7
from gv_db.idempotency import IdempotencyStore, SqlIdempotencyStore
from gv_messaging.envelope import EventEnvelope
from gv_messaging.outbox import OutboxWriter
from rating_service.application.ports import (
    AdminAction,
    AuditLog,
    HistoryRepository,
    HistoryRow,
    Json,
    LadderRepository,
    LadderSummary,
    LockMode,
    ProcessedMatches,
    PublishedLadder,
    RatingRepository,
    RebuildQueue,
    RebuildRequest,
    SeasonRepository,
    UserProjection,
    WeeklyRepository,
    WeeklyRow,
)
from rating_service.domain.events import RatingChanged
from rating_service.domain.ladder import LadderConfig
from rating_service.domain.ladder_line import LadderLine, LadderVersion, VersionStatus
from rating_service.domain.rating import Rating, soft_reset_value
from rating_service.domain.season import ClosedBy, Season, SeasonStatus
from rating_service.infrastructure.db.tables import (
    HISTORY_PARTITION_PREFIX,
    TABLES,
    admin_actions,
    idempotency_keys,
    ladder_versions,
    ladders,
    processed_matches,
    rating_history,
    ratings,
    rebuild_requests,
    seasons,
    users_proj,
    weekly_deltas,
    weekly_tops,
)

_STATUS_ORDER = {VersionStatus.ARCHIVED: 0, VersionStatus.DRAFT: 1, VersionStatus.PUBLISHED: 2}


def history_partition_name(season_id: UUID) -> str:
    """Имя партиции ``rating_history`` сезона."""
    return f"{HISTORY_PARTITION_PREFIX}s{season_id.hex}"


# ---------------------------------------------------------------- ladder-ы
class SqlLadders:
    """Ladder-ы и версии конфигов."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_line(self, key: str, *, for_update: bool = False) -> LadderLine | None:
        """Линия версий или ``None``."""
        stmt = select(ladders).where(ladders.c.key == key)
        if for_update:
            stmt = stmt.with_for_update()
        head = (await self._s.execute(stmt)).first()
        if head is None:
            return None
        rows = await self._s.execute(
            select(ladder_versions)
            .where(ladder_versions.c.ladder == key)
            .order_by(ladder_versions.c.version)
        )
        versions = [
            LadderVersion(
                version=r.version,
                status=VersionStatus(r.status),
                config=LadderConfig.parse(r.config),
                reason=r.reason,
                created_by=r.created_by,
                created_at=r.created_at,
                published_at=r.published_at,
            )
            for r in rows
        ]
        return LadderLine(head.key, head.domain, head.created_at, versions)

    async def save_line(self, line: LadderLine) -> None:
        """Сохранить ladder и версии; порядок «архив → черновик → действующая» не нарушает
        частичных уникальных индексов (одна действующая и один черновик)."""
        await self._s.execute(
            pg_insert(ladders)
            .values(key=line.key, domain=line.domain, created_at=line.created_at)
            .on_conflict_do_nothing(index_elements=[ladders.c.key])
        )
        for v in sorted(line.versions, key=lambda x: (_STATUS_ORDER[x.status], x.version)):
            values = {
                "status": v.status.value,
                "config": v.config.to_dict(),
                "reason": v.reason,
                "created_by": v.created_by,
                "created_at": v.created_at,
                "published_at": v.published_at,
            }
            await self._s.execute(
                pg_insert(ladder_versions)
                .values(ladder=line.key, version=v.version, **values)
                .on_conflict_do_update(
                    index_elements=[ladder_versions.c.ladder, ladder_versions.c.version],
                    set_=values,
                )
            )

    async def summaries(self) -> list[LadderSummary]:
        """Все ladder-ы для админки."""
        rows = await self._s.execute(
            select(
                ladders.c.key,
                func.max(ladder_versions.c.version).filter(ladder_versions.c.status == "published"),
                func.max(ladder_versions.c.version).filter(ladder_versions.c.status == "draft"),
            )
            .select_from(
                ladders.outerjoin(ladder_versions, ladder_versions.c.ladder == ladders.c.key)
            )
            .group_by(ladders.c.key)
            .order_by(ladders.c.key)
        )
        return [LadderSummary(key=r[0], published_version=r[1], draft_version=r[2]) for r in rows]

    async def published(self, key: str) -> PublishedLadder | None:
        """Действующая версия ladder-а."""
        row = (
            await self._s.execute(
                select(ladder_versions.c.version, ladder_versions.c.config).where(
                    ladder_versions.c.ladder == key, ladder_versions.c.status == "published"
                )
            )
        ).first()
        return None if row is None else PublishedLadder(row.version, LadderConfig.parse(row.config))

    async def all_published(self) -> list[PublishedLadder]:
        """Действующие версии всех ladder-ов."""
        rows = await self._s.execute(
            select(ladder_versions.c.version, ladder_versions.c.config)
            .where(ladder_versions.c.status == "published")
            .order_by(ladder_versions.c.ladder)
        )
        return [PublishedLadder(r.version, LadderConfig.parse(r.config)) for r in rows]


# ---------------------------------------------------------------- сезоны
def _season(r: Any) -> Season:
    return Season(
        season_id=r.season_id,
        ladder=r.ladder,
        number=r.number,
        ladder_version=r.ladder_version,
        starts_at=r.starts_at,
        ends_at=r.ends_at,
        status=SeasonStatus(r.status),
        finished_at=r.finished_at,
        closed_by=ClosedBy(r.closed_by) if r.closed_by else None,
        players_count=r.players_count,
    )


class SqlSeasons:
    """Сезоны."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def active(self, ladder: str, *, lock: LockMode | None = None) -> Season | None:
        """Действующий сезон; ``share`` — матчи, ``update`` — закрытие."""
        stmt = select(seasons).where(seasons.c.ladder == ladder, seasons.c.status == "active")
        if lock == "share":
            stmt = stmt.with_for_update(read=True)
        elif lock == "update":
            stmt = stmt.with_for_update()
        row = (await self._s.execute(stmt)).first()
        return None if row is None else _season(row)

    async def get(self, season_id: UUID, *, for_update: bool = False) -> Season | None:
        """Сезон по идентификатору."""
        stmt = select(seasons).where(seasons.c.season_id == season_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._s.execute(stmt)).first()
        return None if row is None else _season(row)

    async def open_seasons(self, ladder: str) -> list[Season]:
        """Плановые и действующие сезоны."""
        rows = await self._s.execute(
            select(seasons)
            .where(seasons.c.ladder == ladder, seasons.c.status.in_(("scheduled", "active")))
            .order_by(seasons.c.starts_at)
        )
        return [_season(r) for r in rows]

    async def latest_finished(self, ladder: str) -> Season | None:
        """Последний завершённый сезон."""
        row = (
            await self._s.execute(
                select(seasons)
                .where(seasons.c.ladder == ladder, seasons.c.status == "finished")
                .order_by(seasons.c.number.desc())
                .limit(1)
            )
        ).first()
        return None if row is None else _season(row)

    async def next_number(self, ladder: str) -> int:
        """Номер следующего сезона."""
        top = await self._s.scalar(
            select(func.coalesce(func.max(seasons.c.number), 0)).where(seasons.c.ladder == ladder)
        )
        return int(top or 0) + 1

    async def add(self, season: Season) -> None:
        """Добавить сезон и партицию истории под него."""
        await self._s.execute(insert(seasons).values(**_season_values(season)))
        name = history_partition_name(season.season_id)
        # Имя и значение — из UUID (не из ввода), поэтому f-строка безопасна.
        await self._s.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF rating_history "
                f"FOR VALUES IN ('{season.season_id}')"
            )
        )

    async def save(self, season: Season) -> None:
        """Сохранить изменения сезона."""
        values = _season_values(season)
        del values["season_id"]
        await self._s.execute(
            update(seasons).where(seasons.c.season_id == season.season_id).values(**values)
        )

    async def overlaps(self, ladder: str, starts_at: datetime, ends_at: datetime) -> bool:
        """Пересекается ли период с любым сезоном (завершённый занимает до ``finished_at``)."""
        s = seasons.c
        found = await self._s.scalar(
            select(s.season_id)
            .where(
                s.ladder == ladder,
                s.starts_at < ends_at,
                starts_at < func.coalesce(s.finished_at, s.ends_at),
            )
            .limit(1)
        )
        return found is not None

    async def due(self, ladder: str, now: datetime) -> list[Season]:
        """Действующие сезоны, у которых вышло время."""
        rows = await self._s.execute(
            select(seasons).where(
                seasons.c.ladder == ladder, seasons.c.status == "active", seasons.c.ends_at <= now
            )
        )
        return [_season(r) for r in rows]

    async def scheduled_ready(self, ladder: str, now: datetime) -> Season | None:
        """Плановый сезон, чьё время наступило."""
        row = (
            await self._s.execute(
                select(seasons)
                .where(
                    seasons.c.ladder == ladder,
                    seasons.c.status == "scheduled",
                    seasons.c.starts_at <= now,
                )
                .order_by(seasons.c.starts_at)
                .limit(1)
            )
        ).first()
        return None if row is None else _season(row)


def _season_values(s: Season) -> dict[str, Any]:
    return {
        "season_id": s.season_id,
        "ladder": s.ladder,
        "number": s.number,
        "ladder_version": s.ladder_version,
        "starts_at": s.starts_at,
        "ends_at": s.ends_at,
        "status": s.status.value,
        "finished_at": s.finished_at,
        "closed_by": s.closed_by.value if s.closed_by else None,
        "players_count": s.players_count,
    }


# ---------------------------------------------------------------- рейтинги
def _rating(r: Any) -> Rating:
    return Rating(
        user_id=r.user_id,
        ladder=r.ladder,
        season_id=r.season_id,
        rating=r.rating,
        matches_total=r.matches_total,
        season_matches=r.season_matches,
        event_version=r.event_version,
        updated_at=r.updated_at,
    )


class SqlRatings:
    """Рейтинги игроков."""

    def __init__(self, session: AsyncSession, *, carry_over_batch: int) -> None:
        self._s = session
        self._batch = carry_over_batch

    async def lock(
        self, ladder: str, season_id: UUID, user_ids: Sequence[UUID]
    ) -> dict[UUID, Rating]:
        """Рейтинги сезона под ``FOR UPDATE`` в порядке user_id."""
        rows = await self._s.execute(
            select(ratings)
            .where(
                ratings.c.ladder == ladder,
                ratings.c.season_id == season_id,
                ratings.c.user_id.in_(list(user_ids)),
            )
            .order_by(ratings.c.user_id)
            .with_for_update()
        )
        return {r.user_id: _rating(r) for r in rows}

    async def many(
        self, ladder: str, season_id: UUID, user_ids: Sequence[UUID]
    ) -> dict[UUID, Rating]:
        """Рейтинги сезона без блокировки (чтение)."""
        if not user_ids:
            return {}
        rows = await self._s.execute(
            select(ratings).where(
                ratings.c.ladder == ladder,
                ratings.c.season_id == season_id,
                ratings.c.user_id.in_(list(user_ids)),
            )
        )
        return {r.user_id: _rating(r) for r in rows}

    async def latest_before(self, user_id: UUID, ladder: str, season_id: UUID) -> Rating | None:
        """Последний рейтинг игрока в другом сезоне."""
        row = (
            await self._s.execute(
                select(ratings)
                .join(seasons, seasons.c.season_id == ratings.c.season_id)
                .where(
                    ratings.c.user_id == user_id,
                    ratings.c.ladder == ladder,
                    ratings.c.season_id != season_id,
                )
                .order_by(seasons.c.number.desc())
                .limit(1)
            )
        ).first()
        return None if row is None else _rating(row)

    async def ensure(self, rating: Rating) -> None:
        """Создать строку рейтинга, если её нет (гонка двух матчей новичка — второй не затирает)."""
        await self._s.execute(
            pg_insert(ratings)
            .values(
                user_id=rating.user_id,
                ladder=rating.ladder,
                season_id=rating.season_id,
                rating=rating.rating,
                matches_total=rating.matches_total,
                season_matches=rating.season_matches,
                event_version=rating.event_version,
                updated_at=rating.updated_at,
            )
            .on_conflict_do_nothing()
        )

    async def save(self, rating: Rating) -> None:
        """Upsert рейтинга."""
        values = {
            "rating": rating.rating,
            "matches_total": rating.matches_total,
            "season_matches": rating.season_matches,
            "event_version": rating.event_version,
            "updated_at": rating.updated_at,
        }
        await self._s.execute(
            pg_insert(ratings)
            .values(
                user_id=rating.user_id, ladder=rating.ladder, season_id=rating.season_id, **values
            )
            .on_conflict_do_update(
                index_elements=[ratings.c.user_id, ratings.c.ladder, ratings.c.season_id],
                set_=values,
            )
        )

    async def carry_over(
        self, old_season: UUID, new_season: UUID, config: LadderConfig, now: datetime
    ) -> int:
        """Скопировать рейтинги в новый сезон с мягким сбросом (пачками по user_id)."""
        reset = config.season.soft_reset
        total = 0
        last: UUID | None = None
        while True:
            stmt = select(ratings).where(ratings.c.season_id == old_season)
            if last is not None:
                stmt = stmt.where(ratings.c.user_id > last)
            rows = (
                await self._s.execute(stmt.order_by(ratings.c.user_id).limit(self._batch))
            ).all()
            if not rows:
                return total
            await self._s.execute(
                pg_insert(ratings)
                .values(
                    [
                        {
                            "user_id": r.user_id,
                            "ladder": r.ladder,
                            "season_id": new_season,
                            "rating": soft_reset_value(r.rating, reset),
                            "matches_total": r.matches_total,
                            "season_matches": 0,
                            "event_version": r.event_version,
                            "updated_at": now,
                        }
                        for r in rows
                    ]
                )
                .on_conflict_do_nothing()
            )
            total += len(rows)
            last = rows[-1].user_id

    async def count(self, season_id: UUID) -> int:
        """Игроков с рейтингом в сезоне."""
        return int(
            await self._s.scalar(
                select(func.count()).select_from(ratings).where(ratings.c.season_id == season_id)
            )
            or 0
        )

    async def get(self, user_id: UUID, ladder: str, season_id: UUID) -> Rating | None:
        """Рейтинг игрока в сезоне."""
        row = (
            await self._s.execute(
                select(ratings).where(
                    ratings.c.user_id == user_id,
                    ratings.c.ladder == ladder,
                    ratings.c.season_id == season_id,
                )
            )
        ).first()
        return None if row is None else _rating(row)

    async def board(
        self, ladder: str, season_id: UUID, *, min_matches: int, calibration_matches: int
    ) -> AsyncIterator[tuple[UUID, int, int]]:
        """Игроки лидерборда пачками по user_id: без калибровки и без удалённых."""
        last: UUID | None = None
        while True:
            stmt = (
                select(ratings.c.user_id, ratings.c.rating, ratings.c.event_version)
                .select_from(
                    ratings.outerjoin(users_proj, users_proj.c.user_id == ratings.c.user_id)
                )
                .where(
                    ratings.c.ladder == ladder,
                    ratings.c.season_id == season_id,
                    ratings.c.season_matches >= min_matches,
                    ratings.c.matches_total >= calibration_matches,
                    func.coalesce(users_proj.c.deleted, False).is_(False),
                )
            )
            if last is not None:
                stmt = stmt.where(ratings.c.user_id > last)
            rows = (
                await self._s.execute(stmt.order_by(ratings.c.user_id).limit(self._batch))
            ).all()
            if not rows:
                return
            for r in rows:
                yield r.user_id, r.rating, r.event_version
            last = rows[-1].user_id


# ---------------------------------------------------------------- история
def _history(r: Any) -> HistoryRow:
    return HistoryRow(
        season_id=r.season_id,
        user_id=r.user_id,
        match_id=r.match_id,
        ladder=r.ladder,
        at=r.at,
        queue_format=r.queue_format,
        outcome=r.outcome,
        old=r.old,
        new=r.new,
        delta=r.delta,
        old_tier=r.old_tier,
        tier=r.tier,
        calibrating=r.calibrating,
        calibration_left=r.calibration_left,
        leaver_penalty=r.leaver_penalty,
    )


class SqlHistory:
    """История рейтинга (партиции по сезону)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, event: RatingChanged) -> None:
        """Записать строку истории по событию."""
        await self._s.execute(
            insert(rating_history).values(
                season_id=event.season_id,
                user_id=event.user_id,
                match_id=event.match_id,
                ladder=event.ladder,
                at=event.at,
                queue_format=event.format,
                outcome=event.outcome,
                old=event.old,
                new=event.new,
                delta=event.delta,
                old_tier=event.old_tier,
                tier=event.tier,
                calibrating=event.calibrating,
                calibration_left=event.calibration_left,
                matches_played=event.matches_played,
                leaver_penalty=event.leaver_penalty,
                event_version=event.version,
            )
        )

    async def page(
        self, user_id: UUID, ladder: str, *, before: tuple[datetime, UUID] | None, limit: int
    ) -> list[HistoryRow]:
        """Строки игрока, новые первыми (keyset по ``(at, match_id)``)."""
        h = rating_history.c
        stmt = select(rating_history).where(h.user_id == user_id, h.ladder == ladder)
        if before is not None:
            stmt = stmt.where(tuple_(h.at, h.match_id) < tuple_(*before))
        rows = await self._s.execute(stmt.order_by(h.at.desc(), h.match_id.desc()).limit(limit))
        return [_history(r) for r in rows]

    async def for_match(self, match_id: UUID) -> list[HistoryRow]:
        """Строки обоих игроков матча."""
        rows = await self._s.execute(
            select(rating_history)
            .where(rating_history.c.match_id == match_id)
            .order_by(rating_history.c.user_id)
        )
        return [_history(r) for r in rows]


# ---------------------------------------------------------------- неделя
class SqlWeekly:
    """Прирост рейтинга по неделям."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add_delta(
        self, ladder: str, week: date, user_id: UUID, *, season_id: UUID, delta: int, now: datetime
    ) -> None:
        """Накопить прирост за неделю."""
        stmt = pg_insert(weekly_deltas).values(
            ladder=ladder,
            week_start=week,
            user_id=user_id,
            season_id=season_id,
            delta=delta,
            updated_at=now,
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    weekly_deltas.c.ladder,
                    weekly_deltas.c.week_start,
                    weekly_deltas.c.user_id,
                ],
                set_={
                    "delta": weekly_deltas.c.delta + stmt.excluded.delta,
                    "season_id": stmt.excluded.season_id,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )

    @staticmethod
    def _eligible(ladder: str, week: date, calibration_matches: int) -> Select[Any]:
        """Положительные приросты недели без калибровки и удалённых."""
        w = weekly_deltas.c
        return (
            select(w.user_id, w.delta, w.season_id)
            .select_from(
                weekly_deltas.join(
                    ratings,
                    and_(
                        ratings.c.user_id == w.user_id,
                        ratings.c.ladder == w.ladder,
                        ratings.c.season_id == w.season_id,
                    ),
                ).outerjoin(users_proj, users_proj.c.user_id == w.user_id)
            )
            .where(
                w.ladder == ladder,
                w.week_start == week,
                w.delta > 0,
                ratings.c.matches_total >= calibration_matches,
                func.coalesce(users_proj.c.deleted, False).is_(False),
            )
        )

    async def top(
        self, ladder: str, week: date, *, limit: int, calibration_matches: int
    ) -> list[WeeklyRow]:
        """Лучшие положительные приросты без калибровки и удалённых."""
        w = weekly_deltas.c
        rows = await self._s.execute(
            self._eligible(ladder, week, calibration_matches)
            .order_by(w.delta.desc(), w.user_id)
            .limit(limit)
        )
        return [WeeklyRow(user_id=r.user_id, delta=r.delta, season_id=r.season_id) for r in rows]

    async def rank_of(
        self, ladder: str, week: date, user_id: UUID, *, calibration_matches: int
    ) -> tuple[int, int] | None:
        """``(прирост, место)`` игрока в топе недели; ``None`` — игрока в топе нет."""
        base = self._eligible(ladder, week, calibration_matches).subquery()
        ranked = select(
            base.c.user_id,
            base.c.delta,
            func.row_number().over(order_by=(base.c.delta.desc(), base.c.user_id)).label("place"),
        ).subquery()
        row = (
            await self._s.execute(
                select(ranked.c.delta, ranked.c.place).where(ranked.c.user_id == user_id)
            )
        ).first()
        return None if row is None else (int(row.delta), int(row.place))

    async def delta_of(self, ladder: str, week: date, user_id: UUID) -> int | None:
        """Прирост игрока за неделю."""
        w = weekly_deltas.c
        return await self._s.scalar(
            select(w.delta).where(w.ladder == ladder, w.week_start == week, w.user_id == user_id)
        )

    async def pending_weeks(self, ladder: str, before: date) -> list[date]:
        """Недели до ``before`` с приростами, ещё не закрытые."""
        w = weekly_deltas.c
        closed = exists().where(
            weekly_tops.c.ladder == w.ladder, weekly_tops.c.week_start == w.week_start
        )
        rows = await self._s.execute(
            select(w.week_start)
            .where(w.ladder == ladder, w.week_start < before, ~closed)
            .distinct()
            .order_by(w.week_start)
        )
        return [r[0] for r in rows]

    async def is_finalized(self, ladder: str, week: date) -> bool:
        """Закрыта ли неделя."""
        found = await self._s.scalar(
            select(weekly_tops.c.ladder).where(
                weekly_tops.c.ladder == ladder, weekly_tops.c.week_start == week
            )
        )
        return found is not None

    async def week_season(self, ladder: str, week: date) -> UUID | None:
        """Сезон последнего прироста недели (по времени обработки)."""
        w = weekly_deltas.c
        return await self._s.scalar(
            select(w.season_id)
            .where(w.ladder == ladder, w.week_start == week)
            .order_by(w.updated_at.desc(), w.user_id)
            .limit(1)
        )

    async def finalize(
        self, ladder: str, week: date, season_id: UUID | None, top: list[Json], now: datetime
    ) -> bool:
        """Закрыть неделю; ``False`` — уже закрыта."""
        row = (
            await self._s.execute(
                pg_insert(weekly_tops)
                .values(
                    ladder=ladder, week_start=week, season_id=season_id, top=top, finalized_at=now
                )
                .on_conflict_do_nothing()
                .returning(weekly_tops.c.ladder)
            )
        ).first()
        return row is not None


# ---------------------------------------------------------------- прочее
class SqlProcessedMatches:
    """Дедупликация по ``match_id`` поверх inbox."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def mark(self, match_id: UUID, ladder: str, now: datetime) -> bool:
        """Отметить матч; ``False`` — уже был."""
        row = (
            await self._s.execute(
                pg_insert(processed_matches)
                .values(match_id=match_id, ladder=ladder, processed_at=now)
                .on_conflict_do_nothing()
                .returning(processed_matches.c.match_id)
            )
        ).first()
        return row is not None


class SqlUsers:
    """Проекция профилей identity."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def profile(self, user_id: UUID, nickname: str, version: int) -> None:
        """Ник; устаревшие версии и удалённые пользователи — мимо."""
        stmt = pg_insert(users_proj).values(
            user_id=user_id, nickname=nickname, version=version, deleted=False
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[users_proj.c.user_id],
                set_={"nickname": nickname, "version": version},
                where=and_(users_proj.c.version < version, users_proj.c.deleted.is_(False)),
            )
        )

    async def forget(self, user_id: UUID, version: int) -> None:
        """``UserDeleted``: ник стёрт, пользователь скрыт."""
        stmt = pg_insert(users_proj).values(
            user_id=user_id, nickname=None, version=version, deleted=True
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[users_proj.c.user_id],
                set_={
                    "nickname": None,
                    "deleted": True,
                    "version": func.greatest(users_proj.c.version, version),
                },
            )
        )

    async def nicknames(self, user_ids: Sequence[UUID]) -> dict[UUID, str]:
        """Ники живых пользователей."""
        if not user_ids:
            return {}
        rows = await self._s.execute(
            select(users_proj.c.user_id, users_proj.c.nickname).where(
                users_proj.c.user_id.in_(list(user_ids)),
                users_proj.c.deleted.is_(False),
                users_proj.c.nickname.is_not(None),
            )
        )
        return {r.user_id: str(r.nickname) for r in rows}

    async def is_visible(self, user_id: UUID) -> bool:
        """Известен ли пользователь и не удалён ли он."""
        found = await self._s.scalar(
            select(users_proj.c.user_id).where(
                users_proj.c.user_id == user_id, users_proj.c.deleted.is_(False)
            )
        )
        return found is not None

    async def deleted(self, user_ids: Sequence[UUID]) -> set[UUID]:
        """Какие из пользователей удалены."""
        if not user_ids:
            return set()
        rows = await self._s.execute(
            select(users_proj.c.user_id).where(
                users_proj.c.user_id.in_(list(user_ids)), users_proj.c.deleted.is_(True)
            )
        )
        return {r[0] for r in rows}

    async def rated_seasons(self, user_id: UUID) -> list[tuple[str, UUID]]:
        """``(ladder, season_id)`` всех рейтингов пользователя."""
        rows = await self._s.execute(
            select(ratings.c.ladder, ratings.c.season_id).where(ratings.c.user_id == user_id)
        )
        return [(r.ladder, r.season_id) for r in rows]


class SqlRebuildQueue:
    """Запросы на пересборку лидерборда."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def request(
        self, ladder: str, season_id: UUID, reason: str, actor_id: UUID, now: datetime
    ) -> UUID:
        """Поставить запрос."""
        request_id = uuid7()
        await self._s.execute(
            insert(rebuild_requests).values(
                id=request_id,
                ladder=ladder,
                season_id=season_id,
                reason=reason,
                requested_by=actor_id,
                created_at=now,
            )
        )
        return request_id

    async def pending(self) -> list[RebuildRequest]:
        """Невыполненные запросы, старые первыми."""
        rows = await self._s.execute(
            select(rebuild_requests)
            .where(rebuild_requests.c.done_at.is_(None))
            .order_by(rebuild_requests.c.created_at, rebuild_requests.c.id)
        )
        return [RebuildRequest(r.id, r.ladder, r.season_id) for r in rows]

    async def complete(self, request_id: UUID, now: datetime, error: str | None = None) -> None:
        """Отметить закрытым (``error`` — закрыт без пересборки)."""
        await self._s.execute(
            update(rebuild_requests)
            .where(rebuild_requests.c.id == request_id)
            .values(done_at=now, error=error)
        )


class SqlAuditLog:
    """Аудит admin-действий."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(self, action: AdminAction) -> None:
        """Записать действие."""
        await self._s.execute(
            insert(admin_actions).values(
                id=uuid7(),
                actor_id=action.actor_id,
                action=action.action,
                target=action.target,
                reason=action.reason,
                details=action.details,
                created_at=action.at,
            )
        )


# ---------------------------------------------------------------- Unit of Work
class SqlRatingUnitOfWork:
    """Транзакция use-case с репозиториями и записью событий в outbox."""

    ladders: LadderRepository
    seasons: SeasonRepository
    ratings: RatingRepository
    history: HistoryRepository
    weekly: WeeklyRepository
    matches: ProcessedMatches
    users: UserProjection
    rebuilds: RebuildQueue
    audit: AuditLog
    idempotency: IdempotencyStore

    def __init__(
        self,
        *,
        producer: str,
        clock: Clock,
        carry_over_batch: int,
        sessions: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        if (sessions is None) == (session is None):
            raise ValueError("нужен ровно один из sessions или session")
        self._sessions = sessions
        self._external = session
        self._outbox = OutboxWriter(TABLES.outbox)
        self._producer = producer
        self._clock = clock
        self._batch = carry_over_batch
        self._session: AsyncSession | None = None
        self._tx: AsyncSessionTransaction | None = None
        self._tracked: list[AggregateRoot[Any]] = []
        self._committed = False

    async def __aenter__(self) -> Self:
        if self._session is not None:
            raise RuntimeError("Unit of Work одноразовый")
        if self._external is not None:
            self._session = self._external
            self._tx = await self._session.begin_nested()
        else:
            assert self._sessions is not None  # noqa: S101 — проверено в __init__
            self._session = self._sessions()
            self._tx = await self._session.begin()
        s = self._session
        self.ladders = SqlLadders(s)
        self.seasons = SqlSeasons(s)
        self.ratings = SqlRatings(s, carry_over_batch=self._batch)
        self.history = SqlHistory(s)
        self.weekly = SqlWeekly(s)
        self.matches = SqlProcessedMatches(s)
        self.users = SqlUsers(s)
        self.rebuilds = SqlRebuildQueue(s)
        self.audit = SqlAuditLog(s)
        self.idempotency = SqlIdempotencyStore(s, idempotency_keys, self._clock)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if not self._committed:
                await self.rollback()
        finally:
            if self._external is None and self._session is not None:
                await self._session.close()

    def track(self, aggregate: AggregateRoot[Any]) -> None:
        """Отметить агрегат: его события попадут в outbox при коммите."""
        if aggregate not in self._tracked:
            self._tracked.append(aggregate)

    async def commit(self) -> None:
        """События — в outbox, затем коммит (или RELEASE SAVEPOINT)."""
        if self._tx is None or self._session is None:
            raise RuntimeError("Unit of Work не открыт")
        envelopes = [
            EventEnvelope.from_domain_event(event, producer=self._producer, clock=self._clock)
            for aggregate in self._tracked
            for event in aggregate.pull_events()
        ]
        await self._outbox.add(self._session, envelopes)
        await self._tx.commit()
        self._committed = True

    async def rollback(self) -> None:
        """Откатить (транзакцию или SAVEPOINT)."""
        if self._tx is not None and self._tx.is_active:
            await self._tx.rollback()
        for aggregate in self._tracked:
            aggregate.pull_events()
