"""Поиск по Postgres (pg_trgm) — fallback, когда OpenSearch выключен или недоступен.

Автокомплит: префикс названия или любого слова алиаса (``LIKE 'q%' OR LIKE '% q%'``) плюс
нечёткое совпадение по слову (``q <% normalized``, опечатки). Ранжирование: совпадение
с начала строки → похожесть слова → популярность. Только ``status = 'active'``.
"""

from __future__ import annotations

from sqlalchemy import Select, String, and_, case, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from catalog_service.application.dto import SearchQuery, Suggestion
from catalog_service.domain.model import MediaDomain
from catalog_service.infrastructure.db.repositories import like_prefix
from catalog_service.infrastructure.db.tables import aliases, media_items


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
        hits = (
            select(
                aliases.c.media_item_id.label("item_id"),
                func.max(case((starts, 1), else_=0)).label("prefix_hit"),
                func.max(func.word_similarity(q, aliases.c.normalized)).label("score"),
            )
            .where(condition)
            .group_by(aliases.c.media_item_id)
            .subquery()
        )
        m = media_items
        stmt = (
            select(m.c.id, m.c.domain, m.c.title, m.c.year, m.c.subtitle)
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
            )
            for r in rows
        ]
