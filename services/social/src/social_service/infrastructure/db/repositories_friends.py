"""Postgres-репозиторий друзей, блокировок, приватности и проекций (поток F1)."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import case, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from social_service.application.ports.friends import (
    BlockRow,
    DeletedFriendship,
    FriendRow,
    PendingView,
    RequestRow,
)
from social_service.domain.friends import Privacy, ordered_pair
from social_service.infrastructure.db.tables_friends import (
    blocks,
    friend_codes,
    friend_requests,
    friendships,
    in_match_proj,
    privacy_settings,
    users_proj,
)

_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"


def _request(r: Any) -> RequestRow:  # Any: строка результата Core
    return RequestRow(
        id=r.id,
        from_user_id=r.from_user_id,
        to_user_id=r.to_user_id,
        status=r.status,
        created_at=r.created_at,
    )


class SqlFriendsRepository:
    """Реализация ``FriendsRepository`` поверх сессии текущей транзакции."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # --- блокировки порядка
    async def _advisory(self, key: str) -> None:
        await self._s.execute(text(_LOCK_SQL), {"k": key})

    async def lock_users(self, *user_ids: UUID) -> None:
        for uid in sorted(set(user_ids), key=lambda u: u.int):
            await self._advisory(f"social:user:{uid}")

    async def lock_pair(self, a: UUID, b: UUID) -> None:
        low, high = ordered_pair(a, b)
        await self._advisory(f"social:pair:{low}:{high}")

    # --- проекция ников
    async def nickname(self, user_id: UUID) -> str | None:
        return await self._s.scalar(
            select(users_proj.c.nickname).where(users_proj.c.user_id == user_id)
        )

    async def user_by_nickname(self, nickname: str) -> UUID | None:
        return await self._s.scalar(
            select(users_proj.c.user_id)
            .where(users_proj.c.nickname_key == nickname.lower())
            .order_by(users_proj.c.user_id)
            .limit(1)
        )

    # --- коды
    async def code_of(self, user_id: UUID) -> tuple[str, datetime] | None:
        row = (
            await self._s.execute(
                select(friend_codes.c.code, friend_codes.c.rotated_at).where(
                    friend_codes.c.user_id == user_id
                )
            )
        ).first()
        return None if row is None else (row.code, row.rotated_at)

    async def user_by_code(self, code: str) -> UUID | None:
        return await self._s.scalar(
            select(friend_codes.c.user_id).where(friend_codes.c.code == code)
        )

    async def create_code(self, user_id: UUID, code: str, now: datetime) -> bool:
        stmt = (
            pg_insert(friend_codes)
            .values(user_id=user_id, code=code, rotated_at=now)
            .on_conflict_do_nothing()
            .returning(friend_codes.c.user_id)
        )
        return (await self._s.execute(stmt)).first() is not None

    async def rotate_code(self, user_id: UUID, code: str, now: datetime) -> bool:
        insert = pg_insert(friend_codes).values(user_id=user_id, code=code, rotated_at=now)
        stmt = insert.on_conflict_do_update(
            index_elements=[friend_codes.c.user_id], set_={"code": code, "rotated_at": now}
        )
        try:
            async with self._s.begin_nested():
                await self._s.execute(stmt)
        except IntegrityError:  # код занят другим игроком (uq_friend_codes_code)
            return False
        return True

    # --- блокировки
    async def blocked(self, blocker: UUID, blocked: UUID) -> bool:
        return (
            await self._s.scalar(
                select(1).where(blocks.c.blocker_id == blocker, blocks.c.blocked_id == blocked)
            )
            is not None
        )

    async def add_block(self, blocker: UUID, blocked: UUID, now: datetime) -> bool:
        stmt = (
            pg_insert(blocks)
            .values(blocker_id=blocker, blocked_id=blocked, created_at=now)
            .on_conflict_do_nothing()
            .returning(blocks.c.blocker_id)
        )
        return (await self._s.execute(stmt)).first() is not None

    async def remove_block(self, blocker: UUID, blocked: UUID) -> bool:
        result = await self._s.execute(
            delete(blocks)
            .where(blocks.c.blocker_id == blocker, blocks.c.blocked_id == blocked)
            .returning(blocks.c.blocker_id)
        )
        return result.first() is not None

    async def blocks_of(self, blocker: UUID) -> list[BlockRow]:
        rows = await self._s.execute(
            select(blocks.c.blocked_id, users_proj.c.nickname, blocks.c.created_at)
            .join(users_proj, users_proj.c.user_id == blocks.c.blocked_id)
            .where(blocks.c.blocker_id == blocker)
            .order_by(blocks.c.created_at.desc(), blocks.c.blocked_id)
        )
        return [BlockRow(r.blocked_id, r.nickname, r.created_at) for r in rows]

    # --- приватность
    async def privacy(self, user_id: UUID) -> Privacy:
        row = (
            await self._s.execute(
                select(
                    privacy_settings.c.profile_public,
                    privacy_settings.c.stats_public,
                    privacy_settings.c.friend_requests,
                ).where(privacy_settings.c.user_id == user_id)
            )
        ).first()
        if row is None:
            return Privacy()
        return Privacy(row.profile_public, row.stats_public, row.friend_requests)

    async def save_privacy(self, user_id: UUID, value: Privacy, now: datetime) -> None:
        values = {
            "profile_public": value.profile_public,
            "stats_public": value.stats_public,
            "friend_requests": value.friend_requests,
            "updated_at": now,
        }
        stmt = pg_insert(privacy_settings).values(user_id=user_id, **values)
        await self._s.execute(
            stmt.on_conflict_do_update(index_elements=[privacy_settings.c.user_id], set_=values)
        )

    # --- дружба
    async def are_friends(self, a: UUID, b: UUID) -> bool:
        low, high = ordered_pair(a, b)
        return (
            await self._s.scalar(
                select(1).where(friendships.c.user_a == low, friendships.c.user_b == high)
            )
            is not None
        )

    async def add_friendship(self, a: UUID, b: UUID, now: datetime) -> bool:
        low, high = ordered_pair(a, b)
        stmt = (
            pg_insert(friendships)
            .values(user_a=low, user_b=high, created_at=now)
            .on_conflict_do_nothing()
            .returning(friendships.c.user_a)
        )
        return (await self._s.execute(stmt)).first() is not None

    async def remove_friendship(self, a: UUID, b: UUID) -> bool:
        low, high = ordered_pair(a, b)
        result = await self._s.execute(
            delete(friendships)
            .where(friendships.c.user_a == low, friendships.c.user_b == high)
            .returning(friendships.c.user_a)
        )
        return result.first() is not None

    async def count_friends(self, user_id: UUID) -> int:
        return int(
            await self._s.scalar(
                select(func.count())
                .select_from(friendships)
                .where(or_(friendships.c.user_a == user_id, friendships.c.user_b == user_id))
            )
            or 0
        )

    async def friends_of(self, user_id: UUID) -> list[FriendRow]:
        other = case(
            (friendships.c.user_a == user_id, friendships.c.user_b), else_=friendships.c.user_a
        )
        rows = await self._s.execute(
            select(other.label("friend_id"), users_proj.c.nickname, friendships.c.created_at)
            .join(users_proj, users_proj.c.user_id == other)
            .where(or_(friendships.c.user_a == user_id, friendships.c.user_b == user_id))
            .order_by(users_proj.c.nickname_key, other)
        )
        return [FriendRow(r.friend_id, r.nickname, r.created_at) for r in rows]

    async def in_match(self, user_ids: Collection[UUID]) -> set[UUID]:
        if not user_ids:
            return set()
        rows = await self._s.scalars(
            select(in_match_proj.c.user_id).where(in_match_proj.c.user_id.in_(list(user_ids)))
        )
        return set(rows)

    # --- заявки
    async def add_request(self, row: RequestRow) -> None:
        low, high = ordered_pair(row.from_user_id, row.to_user_id)
        await self._s.execute(
            friend_requests.insert().values(
                id=row.id,
                from_user_id=row.from_user_id,
                to_user_id=row.to_user_id,
                user_a=low,
                user_b=high,
                status=row.status,
                created_at=row.created_at,
            )
        )

    async def get_request(self, request_id: UUID, *, for_update: bool = False) -> RequestRow | None:
        stmt = select(friend_requests).where(friend_requests.c.id == request_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._s.execute(stmt)).first()
        return None if row is None else _request(row)

    async def pending_from_to(self, from_user: UUID, to_user: UUID) -> RequestRow | None:
        row = (
            await self._s.execute(
                select(friend_requests).where(
                    friend_requests.c.from_user_id == from_user,
                    friend_requests.c.to_user_id == to_user,
                    friend_requests.c.status == "pending",
                )
            )
        ).first()
        return None if row is None else _request(row)

    async def close_request(self, request_id: UUID, status: str, now: datetime) -> None:
        await self._s.execute(
            update(friend_requests)
            .where(friend_requests.c.id == request_id, friend_requests.c.status == "pending")
            .values(status=status, resolved_at=now)
        )

    async def close_pending_between(self, a: UUID, b: UUID, now: datetime) -> int:
        low, high = ordered_pair(a, b)
        result = await self._s.execute(
            update(friend_requests)
            .where(
                friend_requests.c.user_a == low,
                friend_requests.c.user_b == high,
                friend_requests.c.status == "pending",
            )
            .values(status="cancelled", resolved_at=now)
            .returning(friend_requests.c.id)
        )
        return len(result.all())

    async def sent_since(self, user_id: UUID, since: datetime) -> int:
        return int(
            await self._s.scalar(
                select(func.count())
                .select_from(friend_requests)
                .where(
                    friend_requests.c.from_user_id == user_id,
                    friend_requests.c.created_at >= since,
                )
            )
            or 0
        )

    async def pending_of(self, user_id: UUID) -> list[PendingView]:
        other = case(
            (friend_requests.c.from_user_id == user_id, friend_requests.c.to_user_id),
            else_=friend_requests.c.from_user_id,
        )
        rows = await self._s.execute(
            select(
                friend_requests.c.id,
                other.label("other_id"),
                users_proj.c.nickname,
                friend_requests.c.created_at,
                (friend_requests.c.to_user_id == user_id).label("incoming"),
            )
            .join(users_proj, users_proj.c.user_id == other)
            .where(
                friend_requests.c.status == "pending",
                or_(
                    friend_requests.c.from_user_id == user_id,
                    friend_requests.c.to_user_id == user_id,
                ),
            )
            .order_by(friend_requests.c.created_at.desc(), friend_requests.c.id)
        )
        return [PendingView(r.id, r.other_id, r.nickname, r.created_at, r.incoming) for r in rows]

    # --- проекции событий
    async def upsert_user(self, user_id: UUID, nickname: str, version: int, now: datetime) -> bool:
        insert = pg_insert(users_proj).values(
            user_id=user_id,
            nickname=nickname,
            nickname_key=nickname.lower(),
            version=version,
            updated_at=now,
        )
        stmt = insert.on_conflict_do_update(
            index_elements=[users_proj.c.user_id],
            set_={
                "nickname": insert.excluded.nickname,
                "nickname_key": insert.excluded.nickname_key,
                "version": insert.excluded.version,
                "updated_at": insert.excluded.updated_at,
            },
            where=users_proj.c.version < insert.excluded.version,
        ).returning(users_proj.c.user_id)
        return (await self._s.execute(stmt)).first() is not None

    async def match_started(self, match_id: UUID, user_ids: Sequence[UUID], now: datetime) -> None:
        if not user_ids:
            return
        await self._s.execute(
            pg_insert(in_match_proj)
            .values([{"user_id": u, "match_id": match_id, "started_at": now} for u in user_ids])
            .on_conflict_do_nothing()
        )

    async def match_finished(self, match_id: UUID) -> None:
        await self._s.execute(delete(in_match_proj).where(in_match_proj.c.match_id == match_id))

    async def erase_user(self, user_id: UUID) -> list[DeletedFriendship]:
        rows = await self._s.execute(
            delete(friendships)
            .where(or_(friendships.c.user_a == user_id, friendships.c.user_b == user_id))
            .returning(friendships.c.user_a, friendships.c.user_b)
        )
        removed = [DeletedFriendship(r.user_a, r.user_b) for r in rows]
        await self._s.execute(
            delete(friend_requests).where(
                or_(
                    friend_requests.c.from_user_id == user_id,
                    friend_requests.c.to_user_id == user_id,
                )
            )
        )
        await self._s.execute(
            delete(blocks).where(
                or_(blocks.c.blocker_id == user_id, blocks.c.blocked_id == user_id)
            )
        )
        for table in (friend_codes, privacy_settings, users_proj, in_match_proj):
            await self._s.execute(delete(table).where(table.c.user_id == user_id))
        return removed
