"""Поиск по Postgres (pg_trgm) — fallback, когда OpenSearch выключен или недоступен.

Автокомплит: префикс названия или любого слова алиаса (``LIKE 'q%' OR LIKE '% q%'``) плюс
нечёткое совпадение по слову (``q <% normalized``, опечатки). Ранжирование: совпадение
с начала строки → похожесть слова → популярность. Только ``status = 'active'``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    CompoundSelect,
    Select,
    String,
    and_,
    case,
    cast,
    exists,
    func,
    literal,
    or_,
    select,
    union_all,
)
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from catalog_service.application.dto import SearchQuery, Suggestion
from catalog_service.domain.model import MediaDomain
from catalog_service.infrastructure.db.repositories import like_prefix
from catalog_service.infrastructure.db.tables import (
    aliases,
    media_item_persons,
    media_items,
    persons,
)

_SEARCHABLE_ROLES = ("artist", "director")


class TrgmSearch:
    """``SearchBackend`` на pg_trgm."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def autocomplete(self, query: SearchQuery) -> list[Suggestion]:
        """Подсказки по префиксу."""
        return await self._run(self._statement(query, fuzzy_only=False), query.limit)

    async def search(self, query: SearchQuery) -> list[Suggestion]:
        """Нечёткий поиск (включая вхождение подстроки)."""
        return await self._run(self._statement(query, fuzzy_only=True), query.limit)

    @staticmethod
    def _statement(query: SearchQuery, *, fuzzy_only: bool) -> Select[tuple[object, ...]]:
        q = literal(query.normalized, type_=String)
        prefix = like_prefix(query.normalized)
        starts = aliases.c.normalized.like(prefix, escape="\\")
        word_starts = aliases.c.normalized.like("% " + prefix, escape="\\")
        fuzzy = q.op("<%")(aliases.c.normalized)
        if fuzzy_only:
            contains = aliases.c.normalized.like("%" + prefix, escape="\\")
            condition = or_(contains, fuzzy)
        else:
            condition = or_(starts, word_starts, fuzzy)
        by_title = select(
            aliases.c.media_item_id.label("item_id"),
            case((starts, 1), else_=0).label("prefix_hit"),
            func.word_similarity(q, aliases.c.normalized).label("score"),
        ).where(condition)
        scope = query.scope_person
        candidates: Select[Any] | CompoundSelect[Any]
        if scope is not None:
            # Область персоны: только её единицы (из всего каталога), только по названию.
            by_title = by_title.where(
                exists().where(
                    media_item_persons.c.media_item_id == aliases.c.media_item_id,
                    media_item_persons.c.person_id == scope,
                    media_item_persons.c.role.in_(_SEARCHABLE_ROLES),
                )
            )
            candidates = by_title
        else:
            # Поиск ещё и по имени исполнителя или режиссёра; совпавшее по имени ниже
            # совпавшего по названию (prefix_hit = 0).
            name = func.lower(persons.c.name)
            person_condition = or_(
                name.like(prefix, escape="\\"),
                name.like("% " + prefix, escape="\\"),
                q.op("<%")(name),
            )
            by_person = (
                select(
                    media_item_persons.c.media_item_id.label("item_id"),
                    literal(0).label("prefix_hit"),
                    func.word_similarity(q, name).label("score"),
                )
                .join(persons, persons.c.id == media_item_persons.c.person_id)
                .where(media_item_persons.c.role.in_(_SEARCHABLE_ROLES), person_condition)
            )
            candidates = union_all(by_title, by_person)
        found = candidates.subquery()
        hits = (
            select(
                found.c.item_id,
                func.max(found.c.prefix_hit).label("prefix_hit"),
                func.max(found.c.score).label("score"),
            )
            .group_by(found.c.item_id)
            .subquery()
        )
        m = media_items
        creator = (
            select(
                func.string_agg(
                    persons.c.name,
                    aggregate_order_by(literal(", "), media_item_persons.c.position),
                )
            )
            .select_from(
                media_item_persons.join(persons, persons.c.id == media_item_persons.c.person_id)
            )
            .where(
                media_item_persons.c.media_item_id == m.c.id,
                media_item_persons.c.role
                == case((m.c.kind == "track", "artist"), else_="director"),
            )
            .scalar_subquery()
        )
        stmt = (
            select(m.c.id, m.c.domain, m.c.title, m.c.year, m.c.subtitle, creator.label("creator"))
            .join(hits, hits.c.item_id == m.c.id)
            .where(m.c.status == "active")
        )
        if query.domain is not None:
            stmt = stmt.where(and_(m.c.domain == cast(literal(query.domain.value), String)))
        return stmt.order_by(
            hits.c.prefix_hit.desc(), hits.c.score.desc(), m.c.popularity.desc(), m.c.id
        )

    async def _run(self, stmt: Select[tuple[object, ...]], limit: int) -> list[Suggestion]:
        async with self._sessions() as session:
            rows = (await session.execute(stmt.limit(limit))).all()
        return [
            Suggestion(
                id=r.id,
                domain=MediaDomain(r.domain),
                title=r.title,
                year=r.year,
                subtitle=r.subtitle,
                creator=r.creator,
            )
            for r in rows
        ]
