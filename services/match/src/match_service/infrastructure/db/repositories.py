"""Postgres match: история матчей с outbox (D8), оценки, проекции identity и game.

Итог матча — одна транзакция: строка ``matches`` (``ON CONFLICT`` по ``match_id``: повтор
после handoff не пишет ничего), игроки, раунды и событие ``MatchFinished`` в outbox.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gv_core.clock import Clock
from gv_core.domain import DomainEvent
from gv_core.ids import uuid7
from gv_db.idempotency import SqlIdempotencyStore, run_idempotent
from gv_messaging import EventEnvelope, OutboxWriter
from match_service.application.ports import AdminAction, Json, MatchRow, MyMatchRow
from match_service.domain import errors
from match_service.domain.errors import MatchConflictError
from match_service.domain.events import ANONYMOUS, MatchStarted, PlayerRated, match_finished
from match_service.domain.model import MatchState
from match_service.domain.room import Room
from match_service.infrastructure.db.tables import (
    ROUND_PARTITION_PREFIX,
    TABLES,
    admin_actions,
    idempotency_keys,
    match_players,
    match_rounds,
    matches,
    mode_heads,
    player_ratings_given,
    users_proj,
)

PRODUCER = "match"


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


class _Events:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._outbox = OutboxWriter(TABLES.outbox)

    async def emit(self, session: AsyncSession, event: DomainEvent) -> None:
        envelope = EventEnvelope.from_domain_event(event, producer=PRODUCER, clock=self._clock)
        await self._outbox.add(session, [envelope])


class SqlMatchRecords(_Events):
    """``MatchRecords`` на Postgres."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        super().__init__(clock)
        self._sessions = sessions
        self._clock = clock

    def _row(self, event: MatchStarted, meta: Json, *, collections: list[UUID]) -> Json:
        return {
            "match_id": event.match_id,
            "room_code": event.room_code,
            "mode_key": event.mode_key,
            "mode_version": event.mode_version,
            "domain": event.domain,
            "format": event.format,
            "ranked": event.ranked,
            "ladder": event.ladder,
            "meta": meta,
            "collection_ids": collections,
            "players_count": len(event.players),
            "started_at": event.started_at,
            "created_at": self._clock.now(),
        }

    async def record_started(self, event: MatchStarted, meta: Json) -> bool:
        """Строка матча и ``MatchStarted`` (повтор — no-op)."""
        async with self._sessions() as session, session.begin():
            stmt = (
                insert(matches)
                .values(self._row(event, meta, collections=[]))
                .on_conflict_do_nothing(index_elements=[matches.c.match_id])
                .returning(matches.c.match_id)
            )
            if (await session.execute(stmt)).first() is None:
                return False
            await self.emit(session, event)
            return True

    async def record_finished(self, room: Room, match: MatchState) -> bool:
        """Итог одной транзакцией с ``MatchFinished``; ``False`` — уже записан.

        Матч, прерванный до конца отсчёта (``MatchStarted`` не было), не пишется вовсе:
        матча не было. Удалённые к этому моменту аккаунты (``users_proj.deleted``)
        обезличиваются в той же транзакции: строка игрока без ``user_id``, в событии и
        результатах раундов — анонимный идентификатор.
        """
        if match.started_at_ms is None:
            return False
        started = _dt(match.started_at_ms)
        finished = _dt(match.finished_at_ms or match.started_at_ms)
        meta = {
            "title_key": room.mode.title_key,
            "clue_type": room.settings.clue_type,
            "answer_format": room.settings.answer_format,
        }
        base = {
            "match_id": match.match_id,
            "room_code": room.code,
            "mode_key": room.mode.key,
            "mode_version": match.mode_version,
            "domain": room.mode.domain,
            "format": room.mode.kind,
            "ranked": room.mode.ranked,
            "ladder": room.mode.ladder,
            "meta": meta,
            "collection_ids": [c for c, _ in match.collections],
            "players_count": len(match.players),
            "started_at": started,
            "finished_at": finished,
            "finish": match.finish.value if match.finish else None,
            "abort_reason": match.abort_reason,
            "created_at": self._clock.now(),
        }
        async with self._sessions() as session, session.begin():
            hidden = frozenset(
                await session.scalars(
                    select(users_proj.c.user_id).where(
                        users_proj.c.user_id.in_(list(match.players)),
                        users_proj.c.deleted.is_(True),
                    )
                )
            ) | frozenset(room.forgotten)
            event = match_finished(
                room, match, started_at=started, finished_at=finished, hidden=hidden
            )
            stmt = (
                insert(matches)
                .values(base)
                .on_conflict_do_update(
                    index_elements=[matches.c.match_id],
                    set_={
                        "finished_at": finished,
                        "finish": base["finish"],
                        "abort_reason": match.abort_reason,
                        "collection_ids": base["collection_ids"],
                    },
                    where=matches.c.finished_at.is_(None),
                )
                .returning(matches.c.match_id)
            )
            if (await session.execute(stmt)).first() is None:
                return False
            await session.execute(
                insert(match_players),
                [
                    {
                        "match_id": match.match_id,
                        "slot": s.slot,
                        "user_id": None if s.user_id in hidden else s.user_id,
                        "score": s.score,
                        "place": s.place,
                        "outcome": s.outcome,
                        "finish": s.finish.value,
                        "finished_at": finished,
                    }
                    for s in match.standings
                ],
            )
            if match.records:
                await session.execute(
                    insert(match_rounds),
                    [
                        {
                            "match_id": match.match_id,
                            "round_index": r.index,
                            "played_at": started,
                            "media_item_id": r.media_item_id,
                            "void": r.void,
                            "results": [
                                {
                                    "user_id": str(ANONYMOUS if x.user_id in hidden else x.user_id),
                                    "level": x.level,
                                    "partial": x.partial,
                                    "score": x.score,
                                    "elapsed_ms": x.elapsed_ms,
                                }
                                for x in r.results
                            ],
                        }
                        for r in match.records
                    ],
                )
            await self.emit(session, event)
            return True

    async def get(self, match_id: UUID) -> MatchRow | None:
        """Завершённый матч с игроками и раундами."""
        async with self._sessions() as session:
            m = (
                (
                    await session.execute(
                        select(matches).where(
                            matches.c.match_id == match_id, matches.c.finished_at.is_not(None)
                        )
                    )
                )
                .mappings()
                .first()
            )
            if m is None:
                return None
            players = (
                (
                    await session.execute(
                        select(match_players, users_proj.c.nickname)
                        .outerjoin(users_proj, users_proj.c.user_id == match_players.c.user_id)
                        .where(match_players.c.match_id == match_id)
                        .order_by(match_players.c.place, match_players.c.slot)
                    )
                )
                .mappings()
                .all()
            )
            rounds = (
                (
                    await session.execute(
                        select(match_rounds)
                        .where(
                            match_rounds.c.match_id == match_id,
                            match_rounds.c.played_at == m["started_at"],
                        )
                        .order_by(match_rounds.c.round_index)
                    )
                )
                .mappings()
                .all()
            )
        meta = dict(m["meta"] or {})
        return MatchRow(
            match_id=m["match_id"],
            room_code=m["room_code"],
            mode_key=m["mode_key"],
            mode_version=m["mode_version"],
            domain=m["domain"],
            format=m["format"],
            ranked=m["ranked"],
            title_key=str(meta.get("title_key", "")),
            clue_type=str(meta.get("clue_type", "")),
            answer_format=str(meta.get("answer_format", "")),
            started_at=m["started_at"],
            finished_at=m["finished_at"],
            finish=m["finish"],
            collection_ids=list(m["collection_ids"] or []),
            players=[dict(p) for p in players],
            rounds=[dict(r) for r in rounds],
        )

    async def mine(
        self, user_id: UUID, cursor: tuple[datetime, UUID] | None, limit: int
    ) -> list[MyMatchRow]:
        """Матчи пользователя (keyset по ``finished_at``, ``match_id`` убыв.)."""
        mp = match_players
        q = (
            select(mp, matches)
            .join(matches, matches.c.match_id == mp.c.match_id)
            .where(mp.c.user_id == user_id)
            .order_by(mp.c.finished_at.desc(), mp.c.match_id.desc())
            .limit(limit)
        )
        if cursor is not None:
            q = q.where(tuple_(mp.c.finished_at, mp.c.match_id) < tuple_(*cursor))
        async with self._sessions() as session:
            rows = (await session.execute(q)).mappings().all()
        out = []
        for r in rows:
            meta = dict(r["meta"] or {})
            out.append(
                MyMatchRow(
                    match_id=r["match_id"],
                    mode_key=r["mode_key"],
                    mode_version=r["mode_version"],
                    domain=r["domain"],
                    format=r["format"],
                    ranked=r["ranked"],
                    title_key=str(meta.get("title_key", "")),
                    clue_type=str(meta.get("clue_type", "")),
                    answer_format=str(meta.get("answer_format", "")),
                    finished_at=r["finished_at"],
                    finish=r["finish"],
                    players=r["players_count"],
                    place=r["place"],
                    score=r["score"],
                    outcome=r["outcome"],
                    my_finish=r[mp.c.finish],
                )
            )
        return out

    async def participants(self, match_id: UUID) -> list[UUID] | None:
        """Участники завершённого матча."""
        async with self._sessions() as session:
            finished = await session.scalar(
                select(matches.c.match_id).where(
                    matches.c.match_id == match_id, matches.c.finished_at.is_not(None)
                )
            )
            if finished is None:
                return None
            rows = await session.scalars(
                select(match_players.c.user_id).where(
                    match_players.c.match_id == match_id, match_players.c.user_id.is_not(None)
                )
            )
            return [u for u in rows if u is not None]

    async def rate(self, event: PlayerRated, *, actor_id: UUID, key: str, fingerprint: str) -> bool:
        """Оценка + ``PlayerRated`` + ключ идемпотентности одной транзакцией."""
        async with self._sessions() as session, session.begin():
            store = SqlIdempotencyStore(session, idempotency_keys, self._clock)

            async def perform() -> Json:
                inserted = (
                    await session.execute(
                        insert(player_ratings_given)
                        .values(
                            match_id=event.match_id,
                            from_id=event.from_id,
                            to_id=event.to_id,
                            kind=event.kind,
                            reason=event.reason,
                            created_at=event.at,
                        )
                        .on_conflict_do_nothing()
                        .returning(player_ratings_given.c.match_id)
                    )
                ).first()
                if inserted is None:
                    raise MatchConflictError("оценка уже поставлена", code=errors.ALREADY_RATED)
                await self.emit(session, event)
                return {}

            _, executed = await run_idempotent(
                store, actor_id, key, fingerprint, perform, status_code=204
            )
            return executed


class SqlAdminAudit:
    """``AdminAudit``: ``admin_actions`` (повтор запроса с тем же ключом — без новой строки)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record(self, action: AdminAction) -> bool:
        """Записать действие одной транзакцией."""
        async with self._sessions() as session, session.begin():
            row = (
                await session.execute(
                    insert(admin_actions)
                    .values(
                        id=uuid7(),
                        actor_id=action.actor_id,
                        action=action.action,
                        room_code=action.room_code,
                        match_id=action.match_id,
                        reason=action.reason,
                        idempotency_key=action.idempotency_key,
                        created_at=action.at,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[admin_actions.c.actor_id, admin_actions.c.idempotency_key]
                    )
                    .returning(admin_actions.c.id)
                )
            ).first()
            return row is not None


class SqlUsers:
    """Проекция профилей identity (``users_proj``)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession] | None) -> None:
        self._sessions = sessions

    async def nickname(self, user_id: UUID) -> str | None:
        """Ник пользователя."""
        return (await self.nicknames([user_id])).get(user_id)

    async def nicknames(self, user_ids: Sequence[UUID]) -> Mapping[UUID, str]:
        """Ники пользователей (без удалённых)."""
        if self._sessions is None or not user_ids:
            return {}
        async with self._sessions() as session:
            rows = await session.execute(
                select(users_proj.c.user_id, users_proj.c.nickname).where(
                    users_proj.c.user_id.in_(list(user_ids)),
                    users_proj.c.deleted.is_(False),
                    users_proj.c.nickname.is_not(None),
                )
            )
            return {r.user_id: str(r.nickname) for r in rows}

    @staticmethod
    async def profile(session: AsyncSession, user_id: UUID, nickname: str, version: int) -> bool:
        """Ник из ``UserRegistered``/``UserProfileBasicsChanged`` (старые версии — мимо)."""
        stmt = (
            insert(users_proj)
            .values(user_id=user_id, nickname=nickname, version=version, deleted=False)
            .on_conflict_do_update(
                index_elements=[users_proj.c.user_id],
                set_={"nickname": nickname, "version": version},
                where=and_(users_proj.c.version < version, users_proj.c.deleted.is_(False)),
            )
            .returning(users_proj.c.user_id)
        )
        return (await session.execute(stmt)).first() is not None

    @staticmethod
    async def forget(session: AsyncSession, user_id: UUID, version: int) -> None:
        """``UserDeleted``: ник стёрт, история обезличена, оценки удалены."""
        await session.execute(
            insert(users_proj)
            .values(user_id=user_id, nickname=None, version=version, deleted=True)
            .on_conflict_do_update(
                index_elements=[users_proj.c.user_id],
                set_={
                    "nickname": None,
                    "deleted": True,
                    "version": func.greatest(users_proj.c.version, version),
                },
            )
        )
        await session.execute(
            update(match_players).where(match_players.c.user_id == user_id).values(user_id=None)
        )
        await session.execute(
            delete(idempotency_keys).where(idempotency_keys.c.actor_id == user_id)
        )
        # Результаты раундов хранят user_id в JSON: заменить на анонимный (UUID(int=0)).
        await session.execute(
            text(
                "UPDATE match_rounds SET results = ("
                " SELECT jsonb_agg(CASE WHEN e->>'user_id' = CAST(:uid AS text)"
                " THEN jsonb_set(e, '{user_id}', to_jsonb(CAST(:anon AS text))) ELSE e END)"
                " FROM jsonb_array_elements(results) AS e)"
                " WHERE results @> jsonb_build_array("
                "jsonb_build_object('user_id', CAST(:uid AS text)))"
            ),
            {"uid": str(user_id), "anon": str(UUID(int=0))},
        )
        await session.execute(
            delete(player_ratings_given).where(
                or_(
                    player_ratings_given.c.from_id == user_id,
                    player_ratings_given.c.to_id == user_id,
                )
            )
        )


class SqlModeHeads:
    """Текущие версии онлайн-шаблонов (проекция ``game.mode_config.v1``)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession] | None) -> None:
        self._sessions = sessions

    async def current(self, key: str) -> int | None:
        """Опубликованная версия (``None`` — нет или в архиве)."""
        if self._sessions is None:
            return None
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(mode_heads.c.version, mode_heads.c.archived).where(
                        mode_heads.c.mode_key == key
                    )
                )
            ).first()
        if row is None or row.archived:
            return None
        return int(row.version)

    @staticmethod
    async def published(
        session: AsyncSession, key: str, version: int, domain: str, now: datetime
    ) -> None:
        """``ModeConfigPublished``: версия растёт монотонно (откат — тоже новая версия)."""
        await session.execute(
            insert(mode_heads)
            .values(mode_key=key, version=version, domain=domain, archived=False, updated_at=now)
            .on_conflict_do_update(
                index_elements=[mode_heads.c.mode_key],
                set_={"version": version, "domain": domain, "archived": False, "updated_at": now},
                where=mode_heads.c.version <= version,
            )
        )

    @staticmethod
    async def archived(session: AsyncSession, key: str, version: int, now: datetime) -> None:
        """``ModeConfigArchived``: режим убран (если не опубликована более новая версия)."""
        await session.execute(
            update(mode_heads)
            .where(mode_heads.c.mode_key == key, mode_heads.c.version <= version)
            .values(archived=True, updated_at=now)
        )


def partition_name(year: int, month: int) -> str:
    """Имя месячной партиции ``match_rounds``."""
    return f"{ROUND_PARTITION_PREFIX}y{year:04d}m{month:02d}"


def _next(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)  # noqa: PLR2004 — декабрь


async def ensure_partitions(
    sessions: async_sessionmaker[AsyncSession], now: datetime, months_ahead: int
) -> list[str]:
    """Создать месячные партиции ``match_rounds`` с текущего месяца на ``months_ahead`` вперёд.

    Идемпотентно (``IF NOT EXISTS``). Если в ``_default`` уже лежат строки нового месяца,
    ``CREATE ... PARTITION OF`` упадёт — поэтому партиции создаются заранее.
    """
    created: list[str] = []
    year, month = now.year, now.month
    async with sessions() as session, session.begin():
        for _ in range(months_ahead + 1):
            ny, nm = _next(year, month)
            name = partition_name(year, month)
            start, end = f"{year:04d}-{month:02d}-01", f"{ny:04d}-{nm:02d}-01"
            # Имя и границы — из чисел (не из ввода), поэтому f-строка безопасна.
            await session.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF match_rounds "
                    f"FOR VALUES FROM ('{start} 00:00:00+00') TO ('{end} 00:00:00+00')"
                )
            )
            created.append(name)
            year, month = ny, nm
    return created


def as_any(value: Any) -> Any:
    """Значение без проверки типа (строки ``RowMapping``)."""
    return value
