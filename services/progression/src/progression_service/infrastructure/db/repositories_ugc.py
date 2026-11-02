"""Репозитории защиты от накрутки: проекция авторов сборников и метки прохождения."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from progression_service.application.ports import CollectionOwner
from progression_service.infrastructure.db.tables import collection_authors, collection_play_marks


class SqlCollectionAuthors:
    """Проекция «сборник → автор, вид» (``collections.CollectionPublished``)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert(
        self, collection_id: UUID, *, kind: str, author_id: UUID | None, now: datetime
    ) -> None:
        """Записать или обновить сборник (повтор события безопасен)."""
        stmt = pg_insert(collection_authors).values(
            collection_id=collection_id, kind=kind, author_id=author_id, updated_at=now
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[collection_authors.c.collection_id],
                # Старое событие, пришедшее позже нового, запись не затирает.
                where=collection_authors.c.updated_at <= stmt.excluded.updated_at,
                set_={
                    "kind": stmt.excluded.kind,
                    "author_id": stmt.excluded.author_id,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )

    async def get_many(self, collection_ids: Iterable[UUID]) -> dict[UUID, CollectionOwner]:
        """Известные сборники из списка (неизвестных в словаре нет)."""
        ids = list(dict.fromkeys(collection_ids))
        if not ids:
            return {}
        rows = await self._s.execute(
            select(
                collection_authors.c.collection_id,
                collection_authors.c.kind,
                collection_authors.c.author_id,
            ).where(collection_authors.c.collection_id.in_(ids))
        )
        return {r.collection_id: CollectionOwner(r.kind, r.author_id) for r in rows}


class SqlPlayMarks:
    """Метки «игроку засчитана награда за сборник» (окно повтора)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def claim(
        self, user_id: UUID, collection_id: UUID, *, at: datetime, not_after: datetime
    ) -> bool:
        """Атомарно занять награду за сборник.

        Args:
            at: Момент события (его ``finished_at``).
            not_after: Предыдущая награда должна быть не позже этого момента (``at`` минус
                окно повтора); иначе это повтор в окне.

        Returns:
            ``True`` — награда засчитывается (метки не было или окно прошло); ``False`` —
            повтор в окне. Одна команда ``INSERT .. ON CONFLICT DO UPDATE .. WHERE``: два
            параллельных события одного игрока не пройдут оба.
        """
        insert = pg_insert(collection_play_marks).values(
            user_id=user_id, collection_id=collection_id, rewarded_at=at
        )
        stmt = insert.on_conflict_do_update(
            index_elements=[collection_play_marks.c.user_id, collection_play_marks.c.collection_id],
            set_={"rewarded_at": insert.excluded.rewarded_at},
            where=collection_play_marks.c.rewarded_at <= not_after,
        ).returning(collection_play_marks.c.user_id)
        return (await self._s.execute(stmt)).first() is not None
