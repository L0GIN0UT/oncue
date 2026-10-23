"""Проекция ``rating.rating.v1`` в match: ladder-ы и рейтинги игроков (ADR 0038).

Подбору нужны только окна ladder-а и рейтинг игрока, поэтому синхронных вызовов rating нет:
падение rating устаревает проекцию, но не блокирует очередь. Обработчики событий —
статические методы с сессией транзакции ``EventProcessor`` (inbox + изменения атомарно);
повтор и устаревшее событие не меняют проекцию (``version``).

Мягкий сброс сезона применяет сам match: ``SeasonStarted.soft_reset`` пересчитывает рейтинги
строк с другим ``season_id``; ``RatingChanged`` более старого сезона (приехал позже старта
нового — потоки разных ключей не упорядочены) пересчитывается тем же правилом.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from match_service.application.ports import LadderInfo
from match_service.domain.queue import MatchmakingConfig, soft_reset
from match_service.infrastructure.db.repositories import lock_users
from match_service.infrastructure.db.tables import (
    ladders_proj,
    ratings_proj,
    seasons_proj,
    users_proj,
)


def ladder_config(raw: dict[str, Any]) -> MatchmakingConfig:  # Any: JSONB события
    """Блок ``matchmaking`` события → конфиг подбора (KeyError/ValueError — нарушение схемы)."""
    window = raw["window"]
    config = MatchmakingConfig(
        base=int(window["base"]),
        step=int(window["step"]),
        step_every_s=int(window["step_every_s"]),
        max=int(window["max"]),
        slow_hint_after_s=int(raw["slow_hint_after_s"]),
        interval_s=int(raw["interval_s"]),
    )
    if min(config.base, config.step, config.step_every_s, config.max, config.interval_s) < 1:
        raise ValueError("параметры подбора должны быть положительными")
    return config


def _with_season() -> Any:  # Any: Select SQLAlchemy
    """Конфиг ladder-а с ``soft_reset`` текущего сезона (стартовый рейтинг новичка)."""
    return select(
        ladders_proj.c.ladder,
        ladders_proj.c.domain,
        ladders_proj.c.version,
        ladders_proj.c.formats,
        ladders_proj.c.matchmaking,
        seasons_proj.c.soft_reset,
    ).select_from(
        ladders_proj.outerjoin(seasons_proj, seasons_proj.c.ladder == ladders_proj.c.ladder)
    )


class SqlLadders:
    """``LadderProjection`` на Postgres.

    Args:
        sessions: Фабрика сессий.
        default_start_rating: Рейтинг новичка, пока сезон со сбросом не известен.
    """

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession] | None, default_start_rating: int
    ) -> None:
        self._sessions = sessions
        self._default_start = default_start_rating

    def _info(self, row: Any) -> LadderInfo:  # Any: строка SQLAlchemy
        reset = row.soft_reset
        return LadderInfo(
            ladder=row.ladder,
            domain=row.domain,
            version=int(row.version),
            formats=tuple(row.formats),
            matchmaking=ladder_config(row.matchmaking),
            start_rating=int(reset["target"]) if reset else self._default_start,
        )

    async def ladder(self, key: str) -> LadderInfo | None:
        """Ladder по ключу."""
        if self._sessions is None:
            return None
        async with self._sessions() as session:
            row = (
                await session.execute(_with_season().where(ladders_proj.c.ladder == key))
            ).first()
        return self._info(row) if row is not None else None

    async def all(self) -> list[LadderInfo]:
        """Все ladder-ы, по ключу."""
        if self._sessions is None:
            return []
        async with self._sessions() as session:
            rows = (await session.execute(_with_season().order_by(ladders_proj.c.ladder))).all()
        return [self._info(r) for r in rows]

    async def rating(self, user_id: UUID, ladder: str) -> int | None:
        """Рейтинг игрока."""
        if self._sessions is None:
            return None
        async with self._sessions() as session:
            value = (
                await session.execute(
                    select(ratings_proj.c.rating).where(
                        ratings_proj.c.user_id == user_id, ratings_proj.c.ladder == ladder
                    )
                )
            ).scalar_one_or_none()
        return int(value) if value is not None else None

    # ------------------------------------------------------------------ события
    @staticmethod
    async def published(
        session: AsyncSession,
        *,
        ladder: str,
        version: int,
        domain: str,
        formats: list[str],
        matchmaking: dict[str, Any],  # Any: блок matchmaking события
        now: datetime,
    ) -> None:
        """``LadderPublished``: применяется только версия выше текущей."""
        values = {
            "version": version,
            "domain": domain,
            "formats": formats,
            "matchmaking": matchmaking,
            "updated_at": now,
        }
        await session.execute(
            insert(ladders_proj)
            .values(ladder=ladder, **values)
            .on_conflict_do_update(
                index_elements=[ladders_proj.c.ladder],
                set_=values,
                where=ladders_proj.c.version < version,
            )
        )

    @staticmethod
    async def season_started(
        session: AsyncSession,
        *,
        ladder: str,
        season_id: UUID,
        starts_at: datetime,
        reset: dict[str, Any] | None,  # Any: {target, ratio} события
        now: datetime,
    ) -> None:
        """``SeasonStarted``: запомнить сезон и применить мягкий сброс к рейтингам.

        Не зависит от ``LadderPublished``: сезон хранится отдельно (``seasons_proj``), поэтому
        событие, пришедшее раньше конфига ladder-а, не теряет ``soft_reset``.
        """
        values = {
            "season_id": season_id,
            "starts_at": starts_at,
            "soft_reset": reset,
            "updated_at": now,
        }
        await session.execute(
            insert(seasons_proj)
            .values(ladder=ladder, **values)
            .on_conflict_do_update(
                index_elements=[seasons_proj.c.ladder],
                set_=values,
                where=seasons_proj.c.starts_at <= starts_at,  # старый сезон не откатывает новый
            )
        )
        current = (
            await session.execute(
                select(seasons_proj.c.season_id).where(seasons_proj.c.ladder == ladder)
            )
        ).scalar_one()
        if current != season_id:
            return  # событие устаревшего сезона
        stale = (
            ratings_proj.c.ladder == ladder,
            ratings_proj.c.season_id.is_distinct_from(season_id),
        )
        if reset is None:
            await session.execute(update(ratings_proj).where(*stale).values(season_id=season_id))
            return
        target, ratio = int(reset["target"]), float(reset["ratio"])
        rows = (await session.execute(select(ratings_proj).where(*stale))).all()
        for r in rows:
            await session.execute(
                update(ratings_proj)
                .where(ratings_proj.c.user_id == r.user_id, ratings_proj.c.ladder == ladder)
                .values(
                    rating=soft_reset(int(r.rating), target=target, ratio=ratio),
                    season_id=season_id,
                    updated_at=now,
                )
            )

    @staticmethod
    async def rating_changed(
        session: AsyncSession,
        *,
        user_id: UUID,
        ladder: str,
        new: int,
        version: int,
        season_id: UUID,
        at: datetime,
        now: datetime,
    ) -> bool:
        """``RatingChanged``: применяется, только если ``version`` выше сохранённой.

        Удалённый аккаунт (``users_proj.deleted``) пропускается: рейтинг не воскресает после
        ``UserDeleted``. Общий замок пользователя сериализует это с ``SqlUsers.forget``.
        """
        await lock_users(session, [user_id], shared=True)
        deleted = (
            await session.execute(
                select(users_proj.c.deleted).where(users_proj.c.user_id == user_id)
            )
        ).scalar_one_or_none()
        if deleted:
            return False
        season = (
            await session.execute(
                select(
                    seasons_proj.c.season_id,
                    seasons_proj.c.starts_at,
                    seasons_proj.c.soft_reset,
                ).where(seasons_proj.c.ladder == ladder)
            )
        ).first()
        rating, sid = new, season_id
        if season is not None and season.soft_reset is not None and at < season.starts_at:
            # Матч старого сезона обработан после старта нового: значение до сброса.
            rating = soft_reset(
                new,
                target=int(season.soft_reset["target"]),
                ratio=float(season.soft_reset["ratio"]),
            )
            sid = season.season_id
        values = {"rating": rating, "version": version, "season_id": sid, "updated_at": now}
        result = await session.execute(
            insert(ratings_proj)
            .values(user_id=user_id, ladder=ladder, **values)
            .on_conflict_do_update(
                index_elements=[ratings_proj.c.user_id, ratings_proj.c.ladder],
                set_=values,
                where=ratings_proj.c.version < version,
            )
            .returning(ratings_proj.c.user_id)
        )
        return result.first() is not None

    @staticmethod
    async def forget_user(session: AsyncSession, user_id: UUID) -> None:
        """``UserDeleted``: рейтинги удалённого аккаунта стираются."""
        await session.execute(delete(ratings_proj).where(ratings_proj.c.user_id == user_id))
