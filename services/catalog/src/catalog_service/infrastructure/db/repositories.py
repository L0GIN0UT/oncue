"""Репозитории catalog на SQLAlchemy Core и Unit of Work с ними."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import Any, Self

from sqlalchemy import Row, delete, exists, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from catalog_service.application.dto import (
    AdminItemFilter,
    ClueStatus,
    ItemSummary,
    RecordedAction,
)
from catalog_service.domain.clue_texts import ApprovedClueTexts, ClueTexts, ClueTextStatus
from catalog_service.domain.import_job import ImportCounters, ImportJob, ImportSource, ImportStatus
from catalog_service.domain.media_item import MediaItem
from catalog_service.domain.model import (
    Alias,
    AliasKind,
    AliasSource,
    ExternalSource,
    ItemStatus,
    MediaDomain,
    MediaKind,
    MediaMetrics,
    PersonCredit,
    PersonRole,
    SourceMedia,
    SourceMediaKind,
    SourceMediaOrigin,
)
from catalog_service.domain.republish import RepublishJob, RepublishStatus
from catalog_service.infrastructure.db.tables import (
    admin_actions,
    aliases,
    clue_texts,
    external_refs,
    genres,
    idempotency_keys,
    import_jobs,
    media_item_clue_status,
    media_item_genres,
    media_item_persons,
    media_items,
    media_metrics,
    persons,
    republish_jobs,
    source_media_refs,
)
from gv_core.clock import Clock
from gv_core.errors import ConflictError, InvalidInputError
from gv_core.ids import uuid7
from gv_core.pagination import Page, decode_cursor, encode_cursor
from gv_db.idempotency import SqlIdempotencyStore
from gv_db.uow import SqlAlchemyUnitOfWork
from gv_messaging.outbox import OutboxWriter


def like_prefix(value: str) -> str:
    """Шаблон ``LIKE 'value%'`` с экранированием спецсимволов."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


class SqlMediaItemRepository:
    """``MediaItemRepository`` на PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, item_id: uuid.UUID) -> MediaItem | None:
        """Загрузить агрегат целиком."""
        row = (
            await self._s.execute(select(media_items).where(media_items.c.id == item_id))
        ).first()
        return None if row is None else (await self._restore_rows([row]))[0]

    async def get_many(self, item_ids: Sequence[uuid.UUID]) -> list[MediaItem]:
        """Единицы пачкой: строки и дочерние данные - по одному запросу на таблицу (без N+1)."""
        if not item_ids:
            return []
        rows = (
            await self._s.execute(select(media_items).where(media_items.c.id.in_(item_ids)))
        ).all()
        return await self._restore_rows(rows)

    async def find_by_external_ref(
        self, source: ExternalSource, external_id: str
    ) -> MediaItem | None:
        """Единица по внешнему id."""
        item_id = await self._s.scalar(
            select(external_refs.c.media_item_id).where(
                external_refs.c.source == source.value,
                external_refs.c.external_id == external_id,
            )
        )
        return None if item_id is None else await self.get(item_id)

    async def exists(self, item_id: uuid.UUID) -> bool:
        """Есть ли единица."""
        return bool(await self._s.scalar(select(exists().where(media_items.c.id == item_id))))

    async def add(self, item: MediaItem) -> None:
        """Вставить новую единицу со всеми дочерними строками."""
        await self._s.execute(insert(media_items).values(id=item.id, **self._row(item)))
        await self._write_children(item)
        item.persisted_version = item.version

    async def save(self, item: MediaItem) -> None:
        """Обновить единицу с проверкой версии (оптимистичная блокировка)."""
        result = await self._s.execute(
            update(media_items)
            .where(media_items.c.id == item.id, media_items.c.version == item.persisted_version)
            .values(**self._row(item), updated_at=func.now())
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]  # у CursorResult есть rowcount
            raise ConflictError(
                "единицу изменили параллельно, повторите", code="catalog.concurrent_update"
            )
        for table in (aliases, media_item_persons, media_item_genres, source_media_refs):
            await self._s.execute(delete(table).where(table.c.media_item_id == item.id))
        await self._s.execute(delete(external_refs).where(external_refs.c.media_item_id == item.id))
        await self._s.execute(delete(media_metrics).where(media_metrics.c.media_item_id == item.id))
        await self._write_children(item)
        item.persisted_version = item.version

    async def list_page(
        self, flt: AdminItemFilter, cursor: str | None, limit: int
    ) -> Page[ItemSummary]:
        """Страница списка, новые первыми (id — UUID v7, упорядочен по времени)."""
        m = media_items
        query = select(m.c.id, m.c.domain, m.c.kind, m.c.title, m.c.year, m.c.status, m.c.version)
        if flt.domain is not None:
            query = query.where(m.c.domain == flt.domain.value)
        if flt.status is not None:
            query = query.where(m.c.status == flt.status)
        if flt.normalized_q:
            query = query.where(
                exists().where(
                    aliases.c.media_item_id == m.c.id,
                    aliases.c.normalized.like(like_prefix(flt.normalized_q), escape="\\"),
                )
            )
        if cursor is not None:
            position = decode_cursor(cursor)
            try:
                query = query.where(m.c.id < uuid.UUID(str(position["id"])))
            except (KeyError, ValueError) as exc:
                raise InvalidInputError("неверный курсор", code="pagination.bad_cursor") from exc
        rows = (await self._s.execute(query.order_by(m.c.id.desc()).limit(limit + 1))).all()
        summaries = [
            ItemSummary(
                id=r.id,
                domain=MediaDomain(r.domain),
                kind=MediaKind(r.kind),
                title=r.title,
                year=r.year,
                status=r.status,
                version=r.version,
            )
            for r in rows[:limit]
        ]
        next_cursor = encode_cursor({"id": str(rows[limit - 1].id)}) if len(rows) > limit else None
        return Page(summaries, next_cursor)

    async def list_ids_after(
        self, domain: MediaDomain | None, after: uuid.UUID | None, limit: int
    ) -> list[uuid.UUID]:
        """id единиц по возрастанию (первичный ключ) после ``after``."""
        m = media_items
        query = select(m.c.id).order_by(m.c.id).limit(limit)
        if domain is not None:
            query = query.where(m.c.domain == domain.value)
        if after is not None:
            query = query.where(m.c.id > after)
        return list((await self._s.execute(query)).scalars().all())

    # ------------------------------------------------------------------ внутреннее

    @staticmethod
    def _row(item: MediaItem) -> dict[str, Any]:
        return {
            "domain": item.domain.value,
            "kind": item.kind.value,
            "status": item.status.value,
            "title": item.title,
            "original_title": item.original_title,
            "year": item.year,
            "subtitle": item.subtitle,
            "popularity": item.metrics.popularity or 0.0,
            "blocked_reason_code": item.blocked_reason_code,
            "version": item.version,
            "countries": list(item.countries),
            "clue_texts": None if item.clue_texts.is_empty else item.clue_texts.to_contract(),
        }

    async def _write_children(self, item: MediaItem) -> None:
        try:
            await self._insert_children(item)
        except IntegrityError as exc:
            # Внешний id уже принадлежит другой единице: импорт этой сущности — конфликт.
            raise ConflictError(
                "внешний идентификатор уже привязан к другой единице",
                code="catalog.external_ref_taken",
            ) from exc

    async def _insert_children(self, item: MediaItem) -> None:
        if item.aliases:
            await self._s.execute(
                insert(aliases),
                [
                    {
                        "media_item_id": item.id,
                        "normalized": a.normalized,
                        "text": a.text,
                        "lang": a.lang,
                        "kind": a.kind.value,
                        "source": a.source.value,
                        "position": i,
                    }
                    for i, a in enumerate(item.aliases)
                ],
            )
        if item.persons:
            await self._s.execute(
                insert(media_item_persons),
                [
                    {
                        "media_item_id": item.id,
                        "person_id": p.person_id,
                        "role": p.role.value,
                        "position": p.order,
                    }
                    for p in item.persons
                ],
            )
        if item.genres:
            await self._s.execute(
                pg_insert(genres).on_conflict_do_nothing(),
                [{"slug": g} for g in item.genres],
            )
            await self._s.execute(
                insert(media_item_genres),
                [
                    {"media_item_id": item.id, "genre_slug": g, "position": i}
                    for i, g in enumerate(item.genres)
                ],
            )
        refs = item.external_refs
        if refs:
            await self._s.execute(
                insert(external_refs),
                [
                    {"source": src.value, "external_id": ref, "media_item_id": item.id}
                    for src, ref in refs.items()
                ],
            )
        await self._s.execute(
            insert(media_metrics).values(media_item_id=item.id, data=item.metrics.to_contract())
        )
        if item.source_media:
            await self._s.execute(
                insert(source_media_refs),
                [
                    {
                        "id": s.id,
                        "media_item_id": item.id,
                        "kind": s.kind.value,
                        "origin": s.origin.value,
                        "uri": s.uri,
                        "position": i,
                        "kind_position": s.position,
                        "source_url": s.source_url,
                        "license_note": s.license_note,
                    }
                    for i, s in enumerate(item.source_media)
                ],
            )

    async def _restore_rows(self, rows: Sequence[Row[Any]]) -> list[MediaItem]:
        """Собрать агрегаты: дочерние строки всех единиц - одним запросом на таблицу (``IN``)."""
        ids = [row.id for row in rows]
        by_item: dict[str, dict[uuid.UUID, list[Any]]] = {
            name: defaultdict(list) for name in ("aliases", "persons", "genres", "refs", "media")
        }
        for a in await self._s.execute(
            select(aliases).where(aliases.c.media_item_id.in_(ids)).order_by(aliases.c.position)
        ):
            by_item["aliases"][a.media_item_id].append(a)
        for p in await self._s.execute(
            select(media_item_persons.c.media_item_id, media_item_persons.c.person_id)
            .add_columns(media_item_persons.c.role, media_item_persons.c.position, persons.c.name)
            .join(persons, persons.c.id == media_item_persons.c.person_id)
            .where(media_item_persons.c.media_item_id.in_(ids))
            .order_by(media_item_persons.c.position)
        ):
            by_item["persons"][p.media_item_id].append(p)
        for g in await self._s.execute(
            select(media_item_genres.c.media_item_id, media_item_genres.c.genre_slug)
            .where(media_item_genres.c.media_item_id.in_(ids))
            .order_by(media_item_genres.c.position)
        ):
            by_item["genres"][g.media_item_id].append(g)
        for r in await self._s.execute(
            select(
                external_refs.c.media_item_id, external_refs.c.source, external_refs.c.external_id
            ).where(external_refs.c.media_item_id.in_(ids))
        ):
            by_item["refs"][r.media_item_id].append(r)
        metrics_by_item = {
            m.media_item_id: m.data
            for m in await self._s.execute(
                select(media_metrics.c.media_item_id, media_metrics.c.data).where(
                    media_metrics.c.media_item_id.in_(ids)
                )
            )
        }
        for m in await self._s.execute(
            select(source_media_refs)
            .where(source_media_refs.c.media_item_id.in_(ids))
            .order_by(source_media_refs.c.position)
        ):
            by_item["media"][m.media_item_id].append(m)
        return [
            self._build(
                row,
                alias_rows=by_item["aliases"][row.id],
                person_rows=by_item["persons"][row.id],
                genre_rows=by_item["genres"][row.id],
                ref_rows=by_item["refs"][row.id],
                metrics_data=metrics_by_item.get(row.id),
                media_rows=by_item["media"][row.id],
            )
            for row in rows
        ]

    @staticmethod
    def _build(
        row: Row[Any],
        *,
        alias_rows: Sequence[Any],
        person_rows: Sequence[Any],
        genre_rows: Sequence[Any],
        ref_rows: Sequence[Any],
        metrics_data: Any,  # Any: jsonb-колонка
        media_rows: Sequence[Any],
    ) -> MediaItem:
        item_id = row.id
        return MediaItem.restore(
            item_id,
            kind=MediaKind(row.kind),
            status=ItemStatus(row.status),
            version=row.version,
            title=row.title,
            original_title=row.original_title,
            year=row.year,
            blocked_reason_code=row.blocked_reason_code,
            aliases=[
                Alias(a.text, a.normalized, a.lang, AliasKind(a.kind), AliasSource(a.source))
                for a in alias_rows
            ],
            persons=[
                PersonCredit(p.person_id, p.name, PersonRole(p.role), p.position)
                for p in person_rows
            ],
            genres=[g.genre_slug for g in genre_rows],
            external_refs={ExternalSource(r.source): r.external_id for r in ref_rows},
            metrics=MediaMetrics.from_mapping(metrics_data or {}),
            source_media=[
                SourceMedia(
                    m.id,
                    SourceMediaKind(m.kind),
                    SourceMediaOrigin(m.origin),
                    m.uri,
                    position=m.kind_position,
                    source_url=m.source_url,
                    license_note=m.license_note,
                )
                for m in media_rows
            ],
            countries=row.countries or (),
            clue_texts=ApprovedClueTexts.from_mapping(row.clue_texts),
        )


class SqlPersonRepository:
    """``PersonRepository``: персона по стабильному ключу (upsert)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def resolve(self, key: str, name: str) -> uuid.UUID:
        """Id персоны; имя обновляется последним импортом."""
        stmt = pg_insert(persons).values(id=uuid7(), external_key=key[:200], name=name[:200])
        upsert = stmt.on_conflict_do_update(
            index_elements=[persons.c.external_key], set_={"name": stmt.excluded.name}
        ).returning(persons.c.id)
        person_id: uuid.UUID = (await self._s.execute(upsert)).scalar_one()
        return person_id


class SqlImportJobRepository:
    """``ImportJobRepository`` на PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, job_id: uuid.UUID) -> ImportJob | None:
        """Задание по id."""
        row = (await self._s.execute(select(import_jobs).where(import_jobs.c.id == job_id))).first()
        return None if row is None else self._restore(row)

    async def find_by_key(self, actor_id: uuid.UUID, idempotency_key: str) -> ImportJob | None:
        """Задание по ключу идемпотентности пользователя."""
        row = (
            await self._s.execute(
                select(import_jobs).where(
                    import_jobs.c.actor_id == actor_id,
                    import_jobs.c.idempotency_key == idempotency_key,
                )
            )
        ).first()
        return None if row is None else self._restore(row)

    async def add(self, job: ImportJob) -> None:
        """Вставить задание."""
        try:
            await self._s.execute(insert(import_jobs).values(id=job.id, **self._row(job)))
        except IntegrityError as exc:
            raise ConflictError(
                "запрос с этим Idempotency-Key уже выполняется", code="idempotency.in_progress"
            ) from exc

    async def save(self, job: ImportJob) -> None:
        """Обновить состояние."""
        await self._s.execute(
            update(import_jobs)
            .where(import_jobs.c.id == job.id)
            .values(**self._row(job), updated_at=func.now())
        )

    @staticmethod
    def _row(job: ImportJob) -> dict[str, Any]:
        return {
            "source": job.source.value,
            "ref": job.ref,
            "file_id": job.file_id,
            "status": job.status.value,
            "counters": job.counters.as_dict(),
            "actor_id": job.actor_id,
            "reason": job.reason,
            "idempotency_key": job.idempotency_key,
            "error": job.error,
        }

    @staticmethod
    def _restore(row: Row[Any]) -> ImportJob:
        return ImportJob(
            row.id,
            source=ImportSource(row.source),
            ref=row.ref,
            file_id=row.file_id,
            actor_id=row.actor_id,
            reason=row.reason,
            idempotency_key=row.idempotency_key,
            status=ImportStatus(row.status),
            counters=ImportCounters(**{k: int(v) for k, v in row.counters.items()}),
            error=row.error,
        )


class SqlAdminActionLog:
    """``AdminActionLog`` на PostgreSQL (аудит; повторы — ``SqlIdempotencyStore``)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(self, action: RecordedAction) -> None:
        """Записать действие; параллельный запрос с тем же ключом — конфликт."""
        try:
            async with self._s.begin_nested():
                await self._s.execute(
                    insert(admin_actions).values(
                        id=action.id,
                        actor_id=action.actor_id,
                        idempotency_key=action.idempotency_key,
                        action=action.action,
                        target_id=action.target_id,
                        reason=action.reason[:500],
                        request_hash=action.request_hash,
                        response=action.response,
                    )
                )
        except IntegrityError as exc:
            raise ConflictError(
                "запрос с этим Idempotency-Key уже выполняется", code="idempotency.in_progress"
            ) from exc


class SqlClueStatusRepository:
    """``ClueStatusRepository`` на PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(
        self, media_item_id: uuid.UUID, clue_type: str, profile_key: str
    ) -> ClueStatus | None:
        """Текущее состояние (с блокировкой строки до конца транзакции)."""
        t = media_item_clue_status
        row = (
            await self._s.execute(
                select(t)
                .where(
                    t.c.media_item_id == media_item_id,
                    t.c.clue_type == clue_type,
                    t.c.profile_key == profile_key,
                )
                .with_for_update()
            )
        ).first()
        if row is None:
            return None
        return ClueStatus(
            media_item_id=row.media_item_id,
            clue_type=row.clue_type,
            profile_key=row.profile_key,
            asset_set_id=row.asset_set_id,
            status=row.status,
            version=row.version,
            levels_count=row.levels_count,
            error_code=row.error_code,
        )

    async def upsert(self, status: ClueStatus) -> None:
        """Вставить или заменить состояние."""
        t = media_item_clue_status
        values = {
            "media_item_id": status.media_item_id,
            "clue_type": status.clue_type,
            "profile_key": status.profile_key,
            "asset_set_id": status.asset_set_id,
            "status": status.status,
            "version": status.version,
            "levels_count": status.levels_count,
            "error_code": status.error_code,
        }
        stmt = pg_insert(t).values(**values)
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[t.c.media_item_id, t.c.clue_type, t.c.profile_key],
                set_={
                    **{k: stmt.excluded[k] for k in values if k not in _CLUE_PK},
                    "updated_at": func.now(),
                },
            )
        )


_CLUE_PK = frozenset({"media_item_id", "clue_type", "profile_key"})


class SqlClueTextsRepository:
    """``ClueTextsRepository`` на PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, media_item_id: uuid.UUID) -> ClueTexts | None:
        """Тексты с блокировкой строки."""
        row = (
            await self._s.execute(
                select(clue_texts)
                .where(clue_texts.c.media_item_id == media_item_id)
                .with_for_update()
            )
        ).first()
        if row is None:
            return None
        return ClueTexts(
            row.media_item_id,
            plot_raw=row.plot_raw,
            plot_masked=row.plot_masked,
            tagline=row.tagline,
            emoji=row.emoji,
            status=ClueTextStatus(row.status),
            revision=row.revision,
            approved_revision=row.approved_revision,
            persisted=True,
        )

    async def save(self, texts: ClueTexts) -> None:
        """Upsert."""
        values = {
            "media_item_id": texts.media_item_id,
            "plot_raw": texts.plot_raw,
            "plot_masked": texts.plot_masked,
            "tagline": texts.tagline,
            "emoji": texts.emoji,
            "status": texts.status.value,
            "revision": texts.revision,
            "approved_revision": texts.approved_revision,
        }
        stmt = pg_insert(clue_texts).values(**values)
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[clue_texts.c.media_item_id],
                set_={
                    **{k: stmt.excluded[k] for k in values if k != "media_item_id"},
                    "updated_at": func.now(),
                },
            )
        )
        texts.persisted = True


class SqlRepublishJobRepository:
    """``RepublishJobRepository`` на PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, job: RepublishJob) -> None:
        """Вставить."""
        await self._s.execute(insert(republish_jobs).values(id=job.id, **self._row(job)))

    async def get(self, job_id: uuid.UUID) -> RepublishJob | None:
        """Задание с блокировкой строки."""
        row = (
            await self._s.execute(
                select(republish_jobs).where(republish_jobs.c.id == job_id).with_for_update()
            )
        ).first()
        if row is None:
            return None
        return RepublishJob(
            row.id,
            domain=MediaDomain(row.domain) if row.domain else None,
            actor_id=row.actor_id,
            reason=row.reason,
            status=RepublishStatus(row.status),
            cursor=row.cursor,
            published=row.published,
        )

    async def save(self, job: RepublishJob) -> None:
        """Обновить."""
        await self._s.execute(
            update(republish_jobs)
            .where(republish_jobs.c.id == job.id)
            .values(**self._row(job), updated_at=func.now())
        )

    @staticmethod
    def _row(job: RepublishJob) -> dict[str, Any]:
        return {
            "domain": job.domain.value if job.domain else None,
            "status": job.status.value,
            "cursor": job.cursor,
            "published": job.published,
            "actor_id": job.actor_id,
            "reason": job.reason[:500],
        }


class CatalogSqlUnitOfWork(SqlAlchemyUnitOfWork):
    """Unit of Work catalog: транзакция, outbox и репозитории на её сессии."""

    items: SqlMediaItemRepository
    persons: SqlPersonRepository
    imports: SqlImportJobRepository
    actions: SqlAdminActionLog
    idempotency: SqlIdempotencyStore
    clue_status: SqlClueStatusRepository
    clue_texts: SqlClueTextsRepository
    republish: SqlRepublishJobRepository

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        outbox: OutboxWriter,
        *,
        producer: str,
        clock: Clock,
    ) -> None:
        super().__init__(session_factory, outbox, producer=producer, clock=clock)

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        session = self.session
        self.items = SqlMediaItemRepository(session)
        self.persons = SqlPersonRepository(session)
        self.imports = SqlImportJobRepository(session)
        self.actions = SqlAdminActionLog(session)
        self.idempotency = SqlIdempotencyStore(session, idempotency_keys, self._clock)
        self.clue_status = SqlClueStatusRepository(session)
        self.clue_texts = SqlClueTextsRepository(session)
        self.republish = SqlRepublishJobRepository(session)
        return self
