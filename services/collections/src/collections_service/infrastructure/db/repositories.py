"""Репозитории PostgreSQL (SQLAlchemy Core) и Unit of Work collections."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Select,
    String,
    Uuid,
    bindparam,
    cast,
    delete,
    exists,
    func,
    insert,
    literal,
    literal_column,
    or_,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from collections_service.application.ports import (
    AdminAction,
    AdminAuditLog,
    CatalogItemSnapshot,
    CatalogQuery,
    CatalogReads,
    CollectionRepository,
    CollectionRow,
    ItemRow,
    ProjectionRepository,
    ReactionRepository,
    Restriction,
    RestrictionRepository,
    StatsRepository,
)
from collections_service.domain.collection import Collection, CollectionState
from collections_service.domain.model import (
    CollectionBody,
    Domain,
    Draft,
    DraftBody,
    Hidden,
    Kind,
    Status,
)
from collections_service.domain.policy import ItemInfo
from collections_service.infrastructure.db.tables import (
    DIFFICULTY_SORT,
    TABLES,
    admin_actions,
    author_restrictions,
    catalog_items_proj,
    collection_drafts,
    collection_items,
    collection_versions,
    collections,
    completions,
    favorites,
    idempotency_keys,
    likes,
    play_stats,
    users_proj,
)
from gv_core.clock import Clock
from gv_core.domain import AggregateRoot, DomainEvent
from gv_core.ids import uuid7
from gv_db.idempotency import IdempotencyStore, SqlIdempotencyStore
from gv_messaging.envelope import EventEnvelope
from gv_messaging.outbox import OutboxWriter
from gv_text.ugc import normalize_ugc

UNTITLED = "—"
"""Название черновика, если присланное после нормализации пустое (CHECK title <> '')."""
LIVE = (str(Status.PUBLISHED), str(Status.HIDDEN))
TagTerms = Callable[[Sequence[str]], list[str]]


def _like(text_: str) -> str:
    """Подстрока для ILIKE с экранированием ``%``/``_``."""
    escaped = text_.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _tsvector(title: str, rest: str) -> ColumnElement[Any]:
    """``title_tsv``: название (вес A) и описание со словами тегов (вес B), russian+simple."""
    parts = [
        func.setweight(
            func.to_tsvector(cast(literal(cfg), REGCONFIG), value), literal_column(f"'{weight}'")
        )
        for value, weight in ((title, "A"), (rest, "B"))
        for cfg in ("russian", "simple")
    ]
    result: ColumnElement[Any] = parts[0]
    for part in parts[1:]:
        result = result.op("||")(part)
    return result


# ---------------------------------------------------------------- агрегат
class SqlCollectionRepository:
    """Агрегаты сборников."""

    def __init__(self, session: AsyncSession, tag_terms: TagTerms) -> None:
        self._s = session
        self._tag_terms = tag_terms

    async def get(self, collection_id: UUID, *, for_update: bool = False) -> Collection | None:
        """Агрегат с черновиком и текущей версией."""
        stmt = select(collections).where(collections.c.id == collection_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._s.execute(stmt)).mappings().first()
        if row is None:
            return None
        draft_row = (
            (
                await self._s.execute(
                    select(collection_drafts).where(
                        collection_drafts.c.collection_id == collection_id
                    )
                )
            )
            .mappings()
            .first()
        )
        current: CollectionBody | None = None
        if row["content_version"] > 0:
            body = await self._s.scalar(
                select(collection_versions.c.body).where(
                    collection_versions.c.collection_id == collection_id,
                    collection_versions.c.content_version == row["content_version"],
                )
            )
            current = CollectionBody.from_json(dict(body)) if body is not None else None
        draft = (
            Draft(
                revision=draft_row["revision"],
                base_version=draft_row["base_version"],
                body=DraftBody.from_json(dict(draft_row["body"])),
                client_label=draft_row["client_label"],
                updated_at=draft_row["updated_at"],
            )
            if draft_row is not None
            else None
        )
        hidden = (
            Hidden(
                reason=row["hidden_reason"] or "", actor_id=row["hidden_by"], at=row["hidden_at"]
            )
            if row["hidden_at"] is not None
            else None
        )
        state = CollectionState(
            domain=Domain(row["domain"]),
            kind=Kind(row["kind"]),
            status=Status(row["status"]),
            author_id=row["author_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            content_version=row["content_version"],
            current=current,
            draft=draft,
            draft_seq=row["draft_seq"],
            hidden=hidden,
            featured=row["featured"],
            featured_rank=row["featured_rank"],
            first_published_at=row["first_published_at"],
            published_at=row["published_at"],
            deleted_at=row["deleted_at"],
        )
        return Collection(row["id"], state, version=row["version"])

    def _values(self, c: Collection) -> dict[str, Any]:
        s = c.state
        values: dict[str, Any] = {
            "domain": str(s.domain),
            "kind": str(s.kind),
            "status": str(s.status),
            "author_id": s.author_id,
            "version": c.version,
            "content_version": s.content_version,
            "draft_seq": s.draft_seq,
            "featured": s.featured,
            "featured_rank": s.featured_rank,
            "hidden_reason": s.hidden.reason if s.hidden else None,
            "hidden_by": s.hidden.actor_id if s.hidden else None,
            "hidden_at": s.hidden.at if s.hidden else None,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
            "first_published_at": s.first_published_at,
            "published_at": s.published_at,
            "deleted_at": s.deleted_at,
        }
        if s.current is not None:
            body = s.current
            rest = " ".join([body.description or "", *self._tag_terms(body.tags)])
            values |= {
                "title": body.title,
                "description": body.description,
                "tags": list(body.tags),
                "cover_preset": body.cover,
                "default_mode_key": body.default_mode_key,
                "visibility": str(body.visibility),
                "items_count": len(body.items),
                "title_tsv": _tsvector(body.title, rest),
            }
        elif s.draft is not None:
            values |= {
                "title": normalize_ugc(s.draft.body.title)[:200] or UNTITLED,
                "visibility": str(s.draft.body.visibility),
            }
        return values

    async def add(self, collection: Collection) -> None:
        """Вставить агрегат."""
        values = {"id": collection.id, **self._values(collection)}
        await self._s.execute(insert(collections).values(**values))
        await self._write_children(collection)

    async def save(self, collection: Collection) -> None:
        """Сохранить агрегат (строка уже под ``FOR UPDATE`` этой транзакции)."""
        await self._s.execute(
            update(collections)
            .where(collections.c.id == collection.id)
            .values(**self._values(collection))
        )
        await self._write_children(collection)

    async def _write_children(self, c: Collection) -> None:
        s = c.state
        await self._s.execute(
            delete(collection_drafts).where(collection_drafts.c.collection_id == c.id)
        )
        if s.draft is not None:
            await self._s.execute(
                insert(collection_drafts).values(
                    collection_id=c.id,
                    revision=s.draft.revision,
                    base_version=s.draft.base_version,
                    body=s.draft.body.to_json(),
                    items_count=len(s.draft.body.items),
                    client_label=s.draft.client_label,
                    updated_at=s.draft.updated_at,
                )
            )
        if s.status is Status.DELETED:
            await self._s.execute(
                delete(collection_items).where(collection_items.c.collection_id == c.id)
            )
        if s.changed_content and s.current is not None:
            await self._s.execute(
                insert(collection_versions).values(
                    collection_id=c.id,
                    content_version=s.content_version,
                    body=s.current.to_json(),
                    published_at=s.published_at,
                )
            )
            await self._s.execute(
                delete(collection_items).where(collection_items.c.collection_id == c.id)
            )
            if s.current.items:
                await self._s.execute(
                    insert(collection_items),
                    [
                        {
                            "collection_id": c.id,
                            "position": position,
                            "media_item_id": item.media_item_id,
                            "emoji": item.emoji,
                        }
                        for position, item in enumerate(s.current.items)
                    ],
                )
            s.changed_content = False

    async def purge(self, collection_id: UUID) -> None:
        """Удалить сборник целиком (каскад: черновик, версии, реакции)."""
        await self._s.execute(delete(collections).where(collections.c.id == collection_id))

    async def lock_author(self, author_id: UUID) -> None:
        """Advisory lock автора до конца транзакции."""
        key = literal("collections:author:") + cast(literal(str(author_id)), String)
        await self._s.execute(select(func.pg_advisory_xact_lock(func.hashtext(key))))

    async def count_drafts(self, author_id: UUID) -> int:
        """Ни разу не опубликованных сборников автора."""
        value = await self._s.scalar(
            select(func.count())
            .select_from(collections)
            .where(collections.c.author_id == author_id, collections.c.status == "draft")
        )
        return int(value or 0)

    async def ids_by_author(self, author_id: UUID) -> list[UUID]:
        """Все сборники автора."""
        rows = await self._s.scalars(
            select(collections.c.id).where(collections.c.author_id == author_id)
        )
        return list(rows)

    async def ids_with_item(self, media_item_id: UUID, limit: int) -> list[UUID]:
        """Живые сборники с единицей в текущем составе."""
        rows = await self._s.scalars(
            select(collection_items.c.collection_id)
            .join(collections, collections.c.id == collection_items.c.collection_id)
            .where(
                collection_items.c.media_item_id == media_item_id,
                collections.c.status.in_(LIVE),
            )
            .distinct()
            .limit(limit)
        )
        return list(rows)

    async def blocked_pairs(self, limit: int) -> list[tuple[UUID, UUID]]:
        """Пары (сборник, заблокированная единица) в живых составах."""
        rows = await self._s.execute(
            select(collection_items.c.collection_id, collection_items.c.media_item_id)
            .join(
                catalog_items_proj,
                catalog_items_proj.c.media_item_id == collection_items.c.media_item_id,
            )
            .join(collections, collections.c.id == collection_items.c.collection_id)
            .where(catalog_items_proj.c.status == "blocked", collections.c.status.in_(LIVE))
            .limit(limit)
        )
        return [(r.collection_id, r.media_item_id) for r in rows]

    async def like_added(self, collection_id: UUID) -> tuple[int, int]:
        """Счётчик и версия агрегата атомарно."""
        row = (
            await self._s.execute(
                update(collections)
                .where(collections.c.id == collection_id)
                .values(
                    likes_count=collections.c.likes_count + 1,
                    version=collections.c.version + 1,
                )
                .returning(collections.c.version, collections.c.likes_count)
            )
        ).one()
        return int(row.version), int(row.likes_count)

    async def like_removed(self, collection_id: UUID) -> int:
        """Счётчик минус один (не ниже нуля)."""
        value = await self._s.scalar(
            update(collections)
            .where(collections.c.id == collection_id)
            .values(likes_count=func.greatest(collections.c.likes_count - 1, 0))
            .returning(collections.c.likes_count)
        )
        return int(value or 0)

    async def likes_count(self, collection_id: UUID) -> int:
        """Текущий счётчик."""
        value = await self._s.scalar(
            select(collections.c.likes_count).where(collections.c.id == collection_id)
        )
        return int(value or 0)


# ---------------------------------------------------------------- реакции
class SqlReactionRepository:
    """Лайки и избранное."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _add(self, table: Any, user_id: UUID, collection_id: UUID, now: datetime) -> bool:
        result = await self._s.execute(
            pg_insert(table)
            .values(user_id=user_id, collection_id=collection_id, created_at=now)
            .on_conflict_do_nothing()
            .returning(table.c.user_id)
        )
        return result.first() is not None

    async def _remove(self, table: Any, user_id: UUID, collection_id: UUID) -> bool:
        result = await self._s.execute(
            delete(table)
            .where(table.c.user_id == user_id, table.c.collection_id == collection_id)
            .returning(table.c.user_id)
        )
        return result.first() is not None

    async def add_like(self, user_id: UUID, collection_id: UUID, now: datetime) -> bool:
        """Новый лайк."""
        return await self._add(likes, user_id, collection_id, now)

    async def remove_like(self, user_id: UUID, collection_id: UUID) -> bool:
        """Снять лайк."""
        return await self._remove(likes, user_id, collection_id)

    async def add_favorite(self, user_id: UUID, collection_id: UUID, now: datetime) -> bool:
        """В избранное."""
        return await self._add(favorites, user_id, collection_id, now)

    async def remove_favorite(self, user_id: UUID, collection_id: UUID) -> bool:
        """Из избранного."""
        return await self._remove(favorites, user_id, collection_id)

    async def state(self, user_id: UUID, collection_id: UUID) -> tuple[bool, bool]:
        """``(liked, favorite)``."""

        def has(table: Any) -> Any:
            return exists().where(
                table.c.user_id == user_id, table.c.collection_id == collection_id
            )

        row = (await self._s.execute(select(has(likes), has(favorites)))).one()
        return bool(row[0]), bool(row[1])

    async def forget_user(self, user_id: UUID) -> int:
        """Лайки (со снижением счётчиков) и избранное пользователя."""
        removed = list(
            await self._s.scalars(
                delete(likes).where(likes.c.user_id == user_id).returning(likes.c.collection_id)
            )
        )
        if removed:
            await self._s.execute(
                update(collections)
                .where(collections.c.id.in_(removed))
                .values(likes_count=func.greatest(collections.c.likes_count - 1, 0))
            )
        await self._s.execute(delete(favorites).where(favorites.c.user_id == user_id))
        return len(removed)


# ---------------------------------------------------------------- статистика
_POPULAR_SQL = text(
    """
    WITH batch AS (
        SELECT id FROM collections
        WHERE status IN ('published', 'hidden') AND (CAST(:after AS uuid) IS NULL OR id > :after)
        ORDER BY id
        LIMIT :limit
    ), scores AS (
        SELECT b.id,
               COALESCE(SUM(p.likes * :like_w + p.plays * :play_w), 0) AS score
        FROM batch b
        LEFT JOIN play_stats p ON p.collection_id = b.id AND p.day >= :since
        GROUP BY b.id
    ), updated AS (
        UPDATE collections c SET popular_score = s.score
        FROM scores s
        WHERE c.id = s.id AND c.popular_score IS DISTINCT FROM s.score
    )
    SELECT max(id::text)::uuid FROM batch
    """
).bindparams(bindparam("after", type_=Uuid(as_uuid=True)))


class SqlStatsRepository:
    """Завершения, сложность, дневная статистика."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add_completion(
        self,
        user_id: UUID,
        collection_id: UUID,
        content_version: int,
        *,
        ratio: float | None,
        session_id: UUID,
        at: datetime,
    ) -> bool:
        """Первое завершение на версию."""
        result = await self._s.execute(
            pg_insert(completions)
            .values(
                user_id=user_id,
                collection_id=collection_id,
                content_version=content_version,
                ratio=ratio,
                session_id=session_id,
                first_at=at,
            )
            .on_conflict_do_nothing()
            .returning(completions.c.user_id)
        )
        return result.first() is not None

    async def completed(self, user_id: UUID, collection_id: UUID, content_version: int) -> bool:
        """Есть завершение версии."""
        value = await self._s.scalar(
            select(
                exists().where(
                    completions.c.user_id == user_id,
                    completions.c.collection_id == collection_id,
                    completions.c.content_version == content_version,
                )
            )
        )
        return bool(value)

    async def add_play(self, collection_id: UUID, ratio: float | None) -> tuple[int, float, int]:
        """Счётчики завершений и сумма долей очков."""
        row = (
            await self._s.execute(
                update(collections)
                .where(collections.c.id == collection_id)
                .values(
                    plays_count=collections.c.plays_count + 1,
                    ratio_sum=collections.c.ratio_sum + (ratio or 0.0),
                    ratio_count=collections.c.ratio_count + (0 if ratio is None else 1),
                )
                .returning(
                    collections.c.plays_count, collections.c.ratio_sum, collections.c.ratio_count
                )
            )
        ).one()
        return int(row.plays_count), float(row.ratio_sum), int(row.ratio_count)

    async def set_difficulty(self, collection_id: UUID, score: float | None) -> None:
        """Сохранить сложность."""
        await self._s.execute(
            update(collections)
            .where(collections.c.id == collection_id)
            .values(difficulty_score=score)
        )

    async def bump_daily(self, collection_id: UUID, day: date, *, likes: int, plays: int) -> None:
        """Прибавить к дню."""
        stmt = pg_insert(play_stats).values(
            collection_id=collection_id, day=day, likes=likes, plays=plays
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[play_stats.c.collection_id, play_stats.c.day],
                set_={
                    "likes": play_stats.c.likes + stmt.excluded.likes,
                    "plays": play_stats.c.plays + stmt.excluded.plays,
                },
            )
        )

    async def recompute_popular(
        self, since: date, *, like_weight: float, play_weight: float, after: UUID | None, limit: int
    ) -> UUID | None:
        """Порция пересчёта ``popular_score``."""
        value = await self._s.scalar(
            _POPULAR_SQL,
            {
                "after": after,
                "limit": limit,
                "since": since,
                "like_w": like_weight,
                "play_w": play_weight,
            },
        )
        return value if isinstance(value, UUID) else (UUID(str(value)) if value else None)

    async def purge_daily(self, before: date) -> int:
        """Удалить старые дни."""
        result = await self._s.execute(delete(play_stats).where(play_stats.c.day < before))
        return int(getattr(result, "rowcount", 0) or 0)

    async def forget_user(self, user_id: UUID) -> int:
        """Удалить завершения пользователя."""
        result = await self._s.execute(delete(completions).where(completions.c.user_id == user_id))
        return int(getattr(result, "rowcount", 0) or 0)


# ---------------------------------------------------------------- проекции
class SqlProjectionRepository:
    """Проекции catalog и identity."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def items_info(self, ids: Sequence[UUID]) -> dict[UUID, ItemInfo]:
        """Домен и статус единиц."""
        if not ids:
            return {}
        p = catalog_items_proj
        rows = await self._s.execute(
            select(p.c.media_item_id, p.c.domain, p.c.status).where(p.c.media_item_id.in_(set(ids)))
        )
        return {r.media_item_id: ItemInfo(domain=r.domain, status=r.status) for r in rows}

    async def item_version(self, media_item_id: UUID) -> int | None:
        """Версия единицы под блокировкой."""
        value = await self._s.scalar(
            select(catalog_items_proj.c.version)
            .where(catalog_items_proj.c.media_item_id == media_item_id)
            .with_for_update()
        )
        return int(value) if value is not None else None

    async def upsert_item(self, item: CatalogItemSnapshot) -> None:
        """Создать или заменить единицу."""
        values = {
            "media_item_id": item.media_item_id,
            "domain": item.domain,
            "status": item.status,
            "version": item.version,
            "title": item.title,
            "year": item.year,
        }
        stmt = pg_insert(catalog_items_proj).values(**values)
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[catalog_items_proj.c.media_item_id],
                set_={k: stmt.excluded[k] for k in values if k != "media_item_id"},
            )
        )

    async def set_item_status(self, media_item_id: UUID, status: str, version: int) -> None:
        """Сменить статус."""
        await self._s.execute(
            update(catalog_items_proj)
            .where(catalog_items_proj.c.media_item_id == media_item_id)
            .values(status=status, version=version)
        )

    async def item_rows(self, items: Sequence[tuple[UUID, str | None]]) -> list[ItemRow]:
        """Карточки единиц в порядке ``items``."""
        p = catalog_items_proj
        ids = {i for i, _ in items}
        found: dict[UUID, RowMapping] = {}
        if ids:
            rows = await self._s.execute(
                select(p.c.media_item_id, p.c.title, p.c.year, p.c.status).where(
                    p.c.media_item_id.in_(ids)
                )
            )
            found = {r["media_item_id"]: r for r in rows.mappings()}
        result: list[ItemRow] = []
        for position, (media_item_id, emoji) in enumerate(items):
            r = found.get(media_item_id)
            result.append(
                ItemRow(
                    position=position,
                    media_item_id=media_item_id,
                    title=r["title"] if r else None,
                    year=r["year"] if r else None,
                    status=r["status"] if r else None,
                    emoji=emoji,
                )
            )
        return result

    async def user_version(self, user_id: UUID) -> int | None:
        """Версия пользователя под блокировкой."""
        value = await self._s.scalar(
            select(users_proj.c.version).where(users_proj.c.user_id == user_id).with_for_update()
        )
        return int(value) if value is not None else None

    async def upsert_user(self, user_id: UUID, nickname: str | None, version: int) -> None:
        """Ник пользователя."""
        stmt = pg_insert(users_proj).values(user_id=user_id, nickname=nickname, version=version)
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[users_proj.c.user_id],
                set_={"nickname": stmt.excluded.nickname, "version": stmt.excluded.version},
            )
        )


# ---------------------------------------------------------------- ограничения и аудит
class SqlRestrictionRepository:
    """Ограничения авторов."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, user_id: UUID) -> Restriction | None:
        """Ограничение."""
        t = author_restrictions
        row = (await self._s.execute(select(t).where(t.c.user_id == user_id))).mappings().first()
        if row is None:
            return None
        return Restriction(
            user_id=row["user_id"],
            reason=row["reason"],
            actor_id=row["actor_id"],
            at=row["created_at"],
        )

    async def put(self, restriction: Restriction) -> None:
        """Создать или заменить."""
        t = author_restrictions
        stmt = pg_insert(t).values(
            user_id=restriction.user_id,
            reason=restriction.reason,
            actor_id=restriction.actor_id,
            created_at=restriction.at,
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[t.c.user_id],
                set_={
                    "reason": stmt.excluded.reason,
                    "actor_id": stmt.excluded.actor_id,
                    "created_at": stmt.excluded.created_at,
                },
            )
        )

    async def delete(self, user_id: UUID) -> bool:
        """Снять."""
        result = await self._s.execute(
            delete(author_restrictions)
            .where(author_restrictions.c.user_id == user_id)
            .returning(author_restrictions.c.user_id)
        )
        return result.first() is not None


class SqlAdminAuditLog:
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


# ---------------------------------------------------------------- чтение
_c = collections


def _row_select() -> Select[Any]:
    return (
        select(
            _c,
            users_proj.c.nickname,
            collection_drafts.c.revision.label("draft_revision"),
            collection_drafts.c.items_count.label("draft_items"),
        )
        .select_from(_c)
        .outerjoin(users_proj, users_proj.c.user_id == _c.c.author_id)
        .outerjoin(collection_drafts, collection_drafts.c.collection_id == _c.c.id)
    )


def _to_row(r: RowMapping) -> CollectionRow:
    return CollectionRow(
        collection_id=r["id"],
        domain=r["domain"],
        kind=r["kind"],
        status=Status(r["status"]),
        visibility=r["visibility"],
        title=r["title"],
        description=r["description"],
        tags=tuple(r["tags"] or ()),
        cover=r["cover_preset"],
        author_id=r["author_id"],
        nickname=r["nickname"],
        default_mode_key=r["default_mode_key"],
        content_version=r["content_version"],
        items_count=r["items_count"],
        likes_count=r["likes_count"],
        plays_count=r["plays_count"],
        difficulty_score=r["difficulty_score"],
        featured=r["featured"],
        featured_rank=r["featured_rank"],
        popular_score=r["popular_score"],
        hidden_reason=r["hidden_reason"],
        hidden_by=r["hidden_by"],
        hidden_at=r["hidden_at"],
        draft_revision=r["draft_revision"],
        draft_items=r["draft_items"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        first_published_at=r["first_published_at"],
        published_at=r["published_at"],
    )


def sort_columns(sort: str) -> tuple[ColumnElement[Any], ColumnElement[Any], bool]:
    """Выражение сортировки, id и направление (``True`` — по убыванию)."""
    if sort == "popular":
        return _c.c.popular_score, _c.c.id, True
    if sort == "new":
        return _c.c.first_published_at, _c.c.id, True
    if sort == "likes":
        return _c.c.likes_count, _c.c.id, True
    if sort == "difficulty":
        return DIFFICULTY_SORT, _c.c.id, True
    return _c.c.featured_rank, _c.c.id, False


def text_condition(text_: str) -> ColumnElement[bool]:
    """Поиск по ``title_tsv`` (russian/simple) и названию (pg_trgm: слово и подстрока)."""
    q = literal(text_, type_=String)
    russian = func.websearch_to_tsquery(cast(literal("russian"), REGCONFIG), q)
    simple = func.websearch_to_tsquery(cast(literal("simple"), REGCONFIG), q)
    return or_(
        _c.c.title_tsv.op("@@")(russian),
        _c.c.title_tsv.op("@@")(simple),
        q.op("<%")(_c.c.title),
        _c.c.title.ilike(_like(text_), escape="\\"),
    )


def catalog_statement(query: CatalogQuery) -> Select[Any]:
    """SELECT каталога (D10): фильтры, keyset и сортировка — для запроса и EXPLAIN."""
    stmt = _row_select().where(_c.c.status == "published", _c.c.visibility == "public")
    if query.sort == "featured":
        # Так же, как в условии частичного индекса (`featured AND ...`), иначе он не подходит.
        stmt = stmt.where(_c.c.featured)
    if query.domain is not None:
        stmt = stmt.where(_c.c.domain == query.domain)
    if query.mode is not None:
        stmt = stmt.where(_c.c.default_mode_key == query.mode)
    if query.curated is not None:
        stmt = stmt.where(_c.c.kind == ("curated" if query.curated else "ugc"))
    if query.difficulty is not None:
        low, high = query.difficulty
        stmt = stmt.where(_c.c.difficulty_score >= low, _c.c.difficulty_score < high)
    if query.text:
        # Совпадения — отдельным MATERIALIZED-CTE: планировщик плохо оценивает ILIKE/`<%` и
        # иначе выбирает обход индекса сортировки с фильтром (весь каталог на редкое слово);
        # так поиск идёт по GIN (BitmapOr title_tsv + pg_trgm), сортируются только совпадения.
        matches = (
            select(_c.c.id)
            .where(text_condition(query.text))
            .cte("matches")
            .prefix_with("MATERIALIZED")
        )
        stmt = stmt.where(_c.c.id.in_(select(matches.c.id)))
    key, ident, desc = sort_columns(query.sort)
    if query.after is not None:
        value, last_id = query.after
        bound = tuple_(key, ident)
        stmt = stmt.where(
            bound < tuple_(literal(value), literal(last_id))
            if desc
            else bound > tuple_(literal(value), literal(last_id))
        )
    order = (key.desc(), ident.desc()) if desc else (key.asc(), ident.asc())
    return stmt.order_by(*order).limit(query.limit)


class SqlCatalogReads:
    """Карточки и списки."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def row(self, collection_id: UUID) -> CollectionRow | None:
        """Строка (кроме удалённых)."""
        stmt = _row_select().where(_c.c.id == collection_id, _c.c.status != "deleted")
        r = (await self._s.execute(stmt)).mappings().first()
        return _to_row(r) if r is not None else None

    async def items(self, collection_id: UUID) -> list[ItemRow]:
        """Текущий состав."""
        ci, p = collection_items, catalog_items_proj
        rows = await self._s.execute(
            select(ci.c.position, ci.c.media_item_id, ci.c.emoji, p.c.title, p.c.year, p.c.status)
            .select_from(ci)
            .outerjoin(p, p.c.media_item_id == ci.c.media_item_id)
            .where(ci.c.collection_id == collection_id)
            .order_by(ci.c.position)
        )
        return [
            ItemRow(
                position=r.position,
                media_item_id=r.media_item_id,
                title=r.title,
                year=r.year,
                status=r.status,
                emoji=r.emoji,
            )
            for r in rows
        ]

    async def search(self, query: CatalogQuery) -> list[CollectionRow]:
        """Каталог."""
        rows = await self._s.execute(catalog_statement(query))
        return [_to_row(r) for r in rows.mappings()]

    async def favorites(
        self, user_id: UUID, *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[tuple[CollectionRow, datetime]]:
        """Избранное (опубликованные)."""
        f = favorites
        stmt = (
            _row_select()
            .add_columns(f.c.created_at.label("favorited_at"))
            .join(f, f.c.collection_id == _c.c.id)
            .where(f.c.user_id == user_id, _c.c.status == "published")
        )
        if after is not None:
            stmt = stmt.where(tuple_(f.c.created_at, f.c.collection_id) < tuple_(*after))
        stmt = stmt.order_by(f.c.created_at.desc(), f.c.collection_id.desc()).limit(limit)
        rows = await self._s.execute(stmt)
        return [(_to_row(r), r["favorited_at"]) for r in rows.mappings()]

    @staticmethod
    def _by_updated(
        stmt: Select[Any], after: tuple[datetime, UUID] | None, limit: int
    ) -> Select[Any]:
        if after is not None:
            stmt = stmt.where(tuple_(_c.c.updated_at, _c.c.id) < tuple_(*after))
        return stmt.order_by(_c.c.updated_at.desc(), _c.c.id.desc()).limit(limit)

    async def by_author(
        self,
        author_id: UUID,
        *,
        status: Status | None,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[CollectionRow]:
        """Сборники автора."""
        stmt = _row_select().where(_c.c.author_id == author_id, _c.c.status != "deleted")
        if status is not None:
            stmt = stmt.where(_c.c.status == str(status))
        rows = await self._s.execute(self._by_updated(stmt, after, limit))
        return [_to_row(r) for r in rows.mappings()]

    async def admin_list(
        self,
        *,
        text: str | None,
        status: Status | None,
        kind: str | None,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[CollectionRow]:
        """Все, кроме удалённых."""
        stmt = _row_select().where(_c.c.status != "deleted")
        if status is not None:
            stmt = stmt.where(_c.c.status == str(status))
        if kind is not None:
            stmt = stmt.where(_c.c.kind == kind)
        if text:
            pattern = _like(text)
            stmt = stmt.where(
                or_(
                    _c.c.title.ilike(pattern, escape="\\"),
                    users_proj.c.nickname.ilike(pattern, escape="\\"),
                )
            )
        rows = await self._s.execute(self._by_updated(stmt, after, limit))
        return [_to_row(r) for r in rows.mappings()]


# ---------------------------------------------------------------- Unit of Work
class SqlCollectionsUnitOfWork:
    """Транзакция use-case с репозиториями и записью событий в outbox.

    Два режима: своя транзакция (API, задачи) или SAVEPOINT в транзакции consumer, где уже
    стоит отметка inbox (коммит всей транзакции делает ``EventProcessor``).
    """

    collections: CollectionRepository
    reactions: ReactionRepository
    stats: StatsRepository
    projection: ProjectionRepository
    restrictions: RestrictionRepository
    audit: AdminAuditLog
    reads: CatalogReads
    idempotency: IdempotencyStore

    def __init__(
        self,
        *,
        producer: str,
        clock: Clock,
        tag_terms: TagTerms,
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
        self._tag_terms = tag_terms
        self._session: AsyncSession | None = None
        self._tx: AsyncSessionTransaction | None = None
        self._tracked: list[AggregateRoot[Any]] = []
        self._events: list[DomainEvent] = []
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
        self.collections = SqlCollectionRepository(s, self._tag_terms)
        self.reactions = SqlReactionRepository(s)
        self.stats = SqlStatsRepository(s)
        self.projection = SqlProjectionRepository(s)
        self.restrictions = SqlRestrictionRepository(s)
        self.audit = SqlAdminAuditLog(s)
        self.reads = SqlCatalogReads(s)
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

    def emit(self, event: DomainEvent) -> None:
        """Событие без агрегата."""
        self._events.append(event)

    async def commit(self) -> None:
        """События — в outbox, затем коммит (или RELEASE SAVEPOINT)."""
        if self._tx is None or self._session is None:
            raise RuntimeError("Unit of Work не открыт")
        events = [e for a in self._tracked for e in a.pull_events()] + self._events
        self._events = []
        envelopes = [
            EventEnvelope.from_domain_event(e, producer=self._producer, clock=self._clock)
            for e in events
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
        self._events = []
