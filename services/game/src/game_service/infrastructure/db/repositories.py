"""Репозитории PostgreSQL (SQLAlchemy Core), ``CandidatePort`` на проекции и Unit of Work game."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from sqlalchemy import (
    Select,
    and_,
    column,
    delete,
    exists,
    func,
    insert,
    literal,
    or_,
    select,
    text,
    true,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from game_service.application.ports import (
    AdminAction,
    AdminAuditLog,
    AssetRequestRepository,
    BracketStatsRepository,
    CollectionRepository,
    DailySetRepository,
    DailyStatsRepository,
    LadderRepository,
    ModeConfigRepository,
    PlayerRecord,
    ProjectionRepository,
    RecordsRepository,
    SessionRepository,
    SubjectRow,
)
from game_service.domain.asset_requests import AssetRequest, AssetRequestStatus
from game_service.domain.daily import DailySet, DailySetStatus, GeneratedBy
from game_service.domain.modes import ConfigVersion, Draft, ModeLine, VersionStatus, domain_of
from game_service.domain.projection import (
    AssetStatus,
    CatalogItem,
    ClueAssets,
    CollectionItem,
    CollectionProj,
    CollectionStatus,
    ItemStatus,
    LadderProj,
)
from game_service.domain.session import (
    STATE_SCHEMA,
    GameSession,
    PlannedRound,
    PlayerKind,
    PlayerRef,
    Round,
    RoundOption,
    SessionStatus,
    ShownItem,
)
from game_service.infrastructure.db.tables import (
    TABLES,
    admin_actions,
    asset_requests,
    bracket_pick_stats,
    catalog_items,
    clue_assets,
    collection_items_proj,
    collection_proj,
    daily_round_stats,
    daily_set_items,
    daily_sets,
    game_sessions,
    idempotency_keys,
    ladders_proj,
    mode_config_drafts,
    mode_configs,
    player_records,
    score_histogram,
)
from gv_core.clock import Clock
from gv_core.domain import AggregateRoot
from gv_core.ids import uuid7
from gv_db.idempotency import IdempotencyStore, SqlIdempotencyStore
from gv_game_kernel import (
    AssetRef,
    CandidatePort,
    CollectionPort,
    Guess,
    GuessKind,
    MatchVerdict,
    MediaItemRef,
    PersonRef,
    PlayableFor,
    PoolQuery,
    RoundResult,
    RoundState,
    SubjectQuery,
)
from gv_messaging.envelope import EventEnvelope
from gv_messaging.outbox import OutboxWriter


# ---------------------------------------------------------------- конфиги режимов
def _to_version(row: RowMapping) -> ConfigVersion:
    return ConfigVersion(
        key=row["key"],
        version=row["version"],
        domain=row["domain"],
        status=VersionStatus(row["status"]),
        body=dict(row["body"]),
        body_hash=row["body_hash"],
        actor_id=row["actor_id"],
        reason=row["reason"],
        created_at=row["created_at"],
        rolled_back_from=row["rolled_back_from"],
    )


def _to_draft(row: RowMapping) -> Draft:
    return Draft(
        body=dict(row["body"]),
        revision=row["revision"],
        validation=dict(row["validation"]),
        updated_by=row["updated_by"],
        updated_at=row["updated_at"],
    )


class SqlModeConfigRepository:
    """Конфиги режимов."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_line(self, key: str, *, for_update: bool = False) -> ModeLine:
        """Режим с последней версией и черновиком."""
        if for_update:
            # Мутации одного ключа сериализуются: черновика может ещё не быть, строки для
            # FOR UPDATE нет — блокируем ключ advisory lock до конца транзакции.
            await self._s.execute(
                select(func.pg_advisory_xact_lock(func.hashtext(literal("game:mode:") + key)))
            )
        latest = (
            (
                await self._s.execute(
                    select(mode_configs)
                    .where(mode_configs.c.key == key)
                    .order_by(mode_configs.c.version.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        draft = (
            (
                await self._s.execute(
                    select(mode_config_drafts).where(mode_config_drafts.c.key == key)
                )
            )
            .mappings()
            .first()
        )
        return ModeLine(
            key,
            latest=_to_version(latest) if latest else None,
            draft=_to_draft(draft) if draft else None,
        )

    async def save_line(self, line: ModeLine) -> None:
        """Новые версии, архивная отметка, черновик."""
        for v in line.new_versions:
            await self._s.execute(
                insert(mode_configs).values(
                    key=v.key,
                    version=v.version,
                    domain=v.domain,
                    status=str(v.status),
                    body=v.body,
                    body_hash=v.body_hash,
                    actor_id=v.actor_id,
                    reason=v.reason,
                    rolled_back_from=v.rolled_back_from,
                    created_at=v.created_at,
                )
            )
        line.new_versions.clear()
        if line.archived_version is not None:
            await self._s.execute(
                update(mode_configs)
                .where(
                    mode_configs.c.key == line.key, mode_configs.c.version == line.archived_version
                )
                .values(status=str(VersionStatus.ARCHIVED))
            )
            line.archived_version = None
        if line.draft_dirty:
            if line.draft is None:
                await self._s.execute(
                    delete(mode_config_drafts).where(mode_config_drafts.c.key == line.key)
                )
            else:
                d = line.draft
                values = {
                    "body": d.body,
                    "revision": d.revision,
                    "validation": d.validation,
                    "updated_by": d.updated_by,
                    "updated_at": d.updated_at,
                }
                await self._s.execute(
                    pg_insert(mode_config_drafts)
                    .values(key=line.key, **values)
                    .on_conflict_do_update(index_elements=[mode_config_drafts.c.key], set_=values)
                )
            line.draft_dirty = False

    async def get_version(self, key: str, version: int) -> ConfigVersion | None:
        """Версия конфига."""
        row = (
            (
                await self._s.execute(
                    select(mode_configs).where(
                        mode_configs.c.key == key, mode_configs.c.version == version
                    )
                )
            )
            .mappings()
            .first()
        )
        return _to_version(row) if row else None

    async def list_versions(self, key: str) -> list[ConfigVersion]:
        """Все версии по возрастанию."""
        rows = (
            (
                await self._s.execute(
                    select(mode_configs)
                    .where(mode_configs.c.key == key)
                    .order_by(mode_configs.c.version)
                )
            )
            .mappings()
            .all()
        )
        return [_to_version(r) for r in rows]

    def _latest_query(self) -> Select[Any]:
        newest = (
            select(mode_configs.c.key, func.max(mode_configs.c.version).label("version"))
            .group_by(mode_configs.c.key)
            .subquery()
        )
        return (
            select(mode_configs)
            .join(
                newest,
                and_(
                    mode_configs.c.key == newest.c.key, mode_configs.c.version == newest.c.version
                ),
            )
            .order_by(mode_configs.c.key)
        )

    async def list_lines(self) -> list[ModeLine]:
        """Все режимы (последняя версия и черновик), по ключу."""
        latest = {
            r["key"]: _to_version(r)
            for r in (await self._s.execute(self._latest_query())).mappings()
        }
        drafts = {
            r["key"]: _to_draft(r)
            for r in (await self._s.execute(select(mode_config_drafts))).mappings()
        }
        return [
            ModeLine(k, latest=latest.get(k), draft=drafts.get(k))
            for k in sorted(latest.keys() | drafts.keys())
        ]

    async def latest_published(self) -> list[ConfigVersion]:
        """Последние версии в статусе ``published``."""
        rows = (await self._s.execute(self._latest_query())).mappings().all()
        return [v for v in (_to_version(r) for r in rows) if v.status is VersionStatus.PUBLISHED]


# ---------------------------------------------------------------- наборы дня
class SqlDailySetRepository:
    """Наборы дня и их единицы."""

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._s = session
        self._clock = clock

    async def _items(self, keys: Sequence[tuple[str, date]]) -> dict[tuple[str, date], list[UUID]]:
        if not keys:
            return {}
        rows = (
            await self._s.execute(
                select(daily_set_items)
                .where(
                    or_(
                        *(
                            and_(daily_set_items.c.mode_key == k, daily_set_items.c.day == d)
                            for k, d in keys
                        )
                    )
                )
                .order_by(daily_set_items.c.mode_key, daily_set_items.c.day, daily_set_items.c.idx)
            )
        ).mappings()
        result: dict[tuple[str, date], list[UUID]] = {}
        for r in rows:
            result.setdefault((r["mode_key"], r["day"]), []).append(r["media_item_id"])
        return result

    @staticmethod
    def _to_set(row: RowMapping, items: list[UUID]) -> DailySet:
        return DailySet(
            row["mode_key"],
            row["day"],
            row["mode_version"],
            DailySetStatus(row["status"]),
            items,
            generation=row["generation"],
            generated_by=GeneratedBy(row["generated_by"]),
            locked_at=row["locked_at"],
            period=row["period"],
            subject_id=row["subject_id"],
        )

    async def get(self, mode_key: str, day: date, *, for_update: bool = False) -> DailySet | None:
        """Набор на день."""
        query = select(daily_sets).where(daily_sets.c.mode_key == mode_key, daily_sets.c.day == day)
        if for_update:
            query = query.with_for_update()
        row = (await self._s.execute(query)).mappings().first()
        if row is None:
            return None
        items = await self._items([(mode_key, day)])
        return self._to_set(row, items.get((mode_key, day), []))

    async def add(self, daily: DailySet) -> bool:
        """Вставить набор; ``False`` — уже есть."""
        now = self._clock.now()
        result = await self._s.execute(
            pg_insert(daily_sets)
            .values(
                mode_key=daily.mode_key,
                day=daily.day,
                mode_version=daily.mode_version,
                status=str(daily.status),
                generation=daily.generation,
                generated_by=str(daily.generated_by),
                locked_at=daily.locked_at,
                created_at=now,
                updated_at=now,
                period=daily.period,
                subject_id=daily.subject_id,
            )
            .on_conflict_do_nothing()
            .returning(daily_sets.c.mode_key)
        )
        if result.first() is None:
            return False
        await self._write_items(daily)
        return True

    async def _write_items(self, daily: DailySet) -> None:
        await self._s.execute(
            delete(daily_set_items).where(
                daily_set_items.c.mode_key == daily.mode_key, daily_set_items.c.day == daily.day
            )
        )
        if daily.items:
            await self._s.execute(
                insert(daily_set_items),
                [
                    {"mode_key": daily.mode_key, "day": daily.day, "idx": i, "media_item_id": item}
                    for i, item in enumerate(daily.items)
                ],
            )
        daily.items_rewritten = False
        daily.replaced.clear()

    async def save(self, daily: DailySet) -> None:
        """Статус и изменённые единицы."""
        await self._s.execute(
            update(daily_sets)
            .where(daily_sets.c.mode_key == daily.mode_key, daily_sets.c.day == daily.day)
            .values(
                mode_version=daily.mode_version,
                status=str(daily.status),
                generation=daily.generation,
                generated_by=str(daily.generated_by),
                locked_at=daily.locked_at,
                updated_at=self._clock.now(),
                subject_id=daily.subject_id,
            )
        )
        if daily.items_rewritten:
            await self._write_items(daily)
            return
        for index in sorted(daily.replaced):
            await self._s.execute(
                update(daily_set_items)
                .where(
                    daily_set_items.c.mode_key == daily.mode_key,
                    daily_set_items.c.day == daily.day,
                    daily_set_items.c.idx == index,
                )
                .values(media_item_id=daily.items[index])
            )
        daily.replaced.clear()

    async def list_range(self, mode_key: str, day_from: date, day_to: date) -> list[DailySet]:
        """Наборы за период."""
        rows = (
            (
                await self._s.execute(
                    select(daily_sets)
                    .where(
                        daily_sets.c.mode_key == mode_key,
                        daily_sets.c.day >= day_from,
                        daily_sets.c.day <= day_to,
                    )
                    .order_by(daily_sets.c.day)
                )
            )
            .mappings()
            .all()
        )
        items = await self._items([(r["mode_key"], r["day"]) for r in rows])
        return [self._to_set(r, items.get((r["mode_key"], r["day"]), [])) for r in rows]

    async def items_between(self, mode_key: str, day_from: date, day_to: date) -> set[UUID]:
        """Единицы наборов за период."""
        if day_to < day_from:
            return set()
        rows = await self._s.execute(
            select(daily_set_items.c.media_item_id).where(
                daily_set_items.c.mode_key == mode_key,
                daily_set_items.c.day >= day_from,
                daily_set_items.c.day <= day_to,
            )
        )
        return {r for (r,) in rows}

    async def due_for_lock(self, mode_key: str, today: date) -> list[DailySet]:
        """Незаблокированные наборы с днём ≤ ``today`` (под ``FOR UPDATE``)."""
        rows = (
            (
                await self._s.execute(
                    select(daily_sets)
                    .where(
                        daily_sets.c.mode_key == mode_key,
                        daily_sets.c.day <= today,
                        daily_sets.c.status == str(DailySetStatus.SCHEDULED),
                    )
                    .order_by(daily_sets.c.day)
                    .with_for_update()
                )
            )
            .mappings()
            .all()
        )
        items = await self._items([(r["mode_key"], r["day"]) for r in rows])
        return [self._to_set(r, items.get((r["mode_key"], r["day"]), [])) for r in rows]


# ---------------------------------------------------------------- проекции
def _to_item(row: RowMapping) -> CatalogItem:
    return CatalogItem(
        media_item_id=row["media_item_id"],
        domain=row["domain"],
        kind=row["kind"],
        status=ItemStatus(row["status"]),
        version=row["version"],
        title=row["title"],
        year=row["year"],
        aliases=tuple(row["aliases"] or ()),
        title_aliases=tuple(row["title_aliases"] or ()),
        persons=tuple(
            PersonRef(id=UUID(p["id"]), name=p["name"], role=p["role"])
            for p in row["persons"] or ()
        ),
        genres=tuple(row["genres"] or ()),
        popularity=row["popularity"],
        metrics=tuple(sorted((str(k), float(v)) for k, v in (row["metrics"] or {}).items())),
    )


def _to_assets(row: RowMapping) -> ClueAssets:
    return ClueAssets(
        media_item_id=row["media_item_id"],
        clue_type=row["clue_type"],
        params_hash=row["params_hash"],
        asset_set_id=row["asset_set_id"],
        levels_count=row["levels_count"],
        status=AssetStatus(row["status"]),
        version=row["version"],
        scope_id=row["scope_id"],
    )


def _pool_filter(query: PoolQuery) -> list[Any]:
    c = catalog_items.c
    conditions: list[Any] = [c.domain == query.domain, c.status == str(ItemStatus.ACTIVE)]
    if query.kinds:
        conditions.append(c.kind.in_(sorted(query.kinds)))
    if query.genres_any:
        conditions.append(c.genres.overlap(sorted(query.genres_any)))
    if query.year_from is not None:
        conditions.append(c.year >= query.year_from)
    if query.year_to is not None:
        conditions.append(c.year <= query.year_to)
    if query.min_popularity is not None:
        conditions.append(c.popularity >= query.min_popularity)
    if query.exclude_ids:
        conditions.append(c.media_item_id.not_in(sorted(query.exclude_ids)))
    if query.playable_for is not None:
        conditions.append(_ready_for(query.playable_for))
    if query.person_ids_any:
        conditions.append(c.person_ids.overlap(sorted(query.person_ids_any)))
    if query.has_metric is not None:
        conditions.append(c.metrics.has_key(query.has_metric))
    return conditions


def _scope_filter(scope: UUID | None) -> Any:
    """Чьи наборы годятся: каталожные и, если задан сборник, его свои (ADR 0029)."""
    a = clue_assets.c
    if scope is None:
        return a.scope_id.is_(None)
    return or_(a.scope_id.is_(None), a.scope_id == scope)


def _ready_for(playable: PlayableFor) -> Any:
    """Есть готовый набор ассетов под параметры загадки (D7): каталожный или, при
    ``playable.scope``, набор этого сборника. Наборы сборников в каталожный пул не входят."""
    a, c = clue_assets.c, catalog_items.c
    return exists().where(
        a.media_item_id == c.media_item_id,
        a.clue_type == playable.clue_type,
        a.params_hash == playable.params_hash,
        a.status == str(AssetStatus.READY),
        _scope_filter(playable.scope),
    )


def _candidate_filter(query: PoolQuery, also: PlayableFor | None) -> list[Any]:
    """Фильтр пула и, если задан, второй набор ассетов (ревил другого типа, ADR 0026)."""
    conditions = _pool_filter(query)
    if also is not None:
        conditions.append(_ready_for(also))
    return conditions


def _like(text_: str) -> str:
    return "%" + text_.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class SqlProjectionRepository:
    """Проекции каталога и ассетов."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_item(
        self, media_item_id: UUID, *, for_update: bool = False
    ) -> CatalogItem | None:
        """Единица."""
        query = select(catalog_items).where(catalog_items.c.media_item_id == media_item_id)
        if for_update:
            query = query.with_for_update()
        row = (await self._s.execute(query)).mappings().first()
        return _to_item(row) if row else None

    async def get_items(self, ids: Sequence[UUID]) -> dict[UUID, CatalogItem]:
        """Единицы по списку."""
        if not ids:
            return {}
        rows = (
            await self._s.execute(
                select(catalog_items).where(catalog_items.c.media_item_id.in_(list(set(ids))))
            )
        ).mappings()
        return {r["media_item_id"]: _to_item(r) for r in rows}

    async def upsert_item(self, item: CatalogItem) -> None:
        """Создать или заменить единицу."""
        values = {
            "domain": item.domain,
            "kind": item.kind,
            "status": str(item.status),
            "version": item.version,
            "title": item.title,
            "year": item.year,
            "aliases": list(item.aliases),
            "title_aliases": list(item.title_aliases),
            "persons": [{"id": str(p.id), "name": p.name, "role": p.role} for p in item.persons],
            "genres": list(item.genres),
            "popularity": item.popularity,
            "metrics": dict(item.metrics),
            "person_ids": list(item.person_ids),
        }
        await self._s.execute(
            pg_insert(catalog_items)
            .values(media_item_id=item.media_item_id, **values)
            .on_conflict_do_update(index_elements=[catalog_items.c.media_item_id], set_=values)
        )

    async def set_item_status(self, media_item_id: UUID, status: str, version: int) -> None:
        """Статус и версия."""
        await self._s.execute(
            update(catalog_items)
            .where(catalog_items.c.media_item_id == media_item_id)
            .values(status=status, version=version)
        )

    async def item_asset_version(self, media_item_id: UUID, *, lock: bool = False) -> int | None:
        """Максимальная версия событий ассетов единицы."""
        if lock:
            await self._s.execute(
                select(clue_assets.c.media_item_id)
                .where(clue_assets.c.media_item_id == media_item_id)
                .with_for_update()
            )
        value = await self._s.scalar(
            select(func.max(clue_assets.c.version)).where(
                clue_assets.c.media_item_id == media_item_id
            )
        )
        return int(value) if value is not None else None

    async def upsert_assets(self, assets: ClueAssets) -> None:
        """Набор под ``(media_item_id, clue_type, params_hash, scope_id)``."""
        values = {
            "asset_set_id": assets.asset_set_id,
            "levels_count": assets.levels_count,
            "status": str(assets.status),
            "version": assets.version,
        }
        await self._s.execute(
            pg_insert(clue_assets)
            .values(
                media_item_id=assets.media_item_id,
                clue_type=assets.clue_type,
                params_hash=assets.params_hash,
                scope_id=assets.scope_id,
                **values,
            )
            .on_conflict_do_update(
                index_elements=[
                    clue_assets.c.media_item_id,
                    clue_assets.c.clue_type,
                    clue_assets.c.params_hash,
                    clue_assets.c.scope_id,
                ],
                set_=values,
            )
        )

    async def delete_scoped_assets(self, scope_id: UUID, keep: Collection[UUID]) -> int:
        """Убрать наборы сборника ``scope_id``, кроме наборов единиц ``keep``; число строк."""
        query = delete(clue_assets).where(clue_assets.c.scope_id == scope_id)
        if keep:
            query = query.where(clue_assets.c.media_item_id.not_in(sorted(keep)))
        result = await self._s.execute(query.returning(clue_assets.c.asset_set_id))
        return len(result.all())

    async def fail_assets(self, asset_set_id: UUID, version: int) -> bool:
        """Отметить набор ``failed``."""
        result = await self._s.execute(
            update(clue_assets)
            .where(clue_assets.c.asset_set_id == asset_set_id)
            .values(status=str(AssetStatus.FAILED), version=version)
            .returning(clue_assets.c.asset_set_id)
        )
        return result.first() is not None

    async def ready_assets(
        self,
        ids: Sequence[UUID],
        clue_type: str,
        params_hash: str,
        *,
        scope: UUID | None = None,
    ) -> dict[UUID, ClueAssets]:
        """Готовые наборы активных единиц: при ``scope`` набор сборника берётся первым,
        иначе каталожный (ADR 0029); без ``scope`` — только каталожные."""
        if not ids:
            return {}
        a, c = clue_assets.c, catalog_items.c
        rows = (
            await self._s.execute(
                select(clue_assets)
                .join(catalog_items, c.media_item_id == a.media_item_id)
                .where(
                    a.media_item_id.in_(list(set(ids))),
                    a.clue_type == clue_type,
                    a.params_hash == params_hash,
                    a.status == str(AssetStatus.READY),
                    c.status == str(ItemStatus.ACTIVE),
                    _scope_filter(scope),
                )
            )
        ).mappings()
        found: dict[UUID, ClueAssets] = {}
        for row in rows:
            assets = _to_assets(row)
            current = found.get(assets.media_item_id)
            if current is None or current.scope_id is None:
                found[assets.media_item_id] = assets
        return found

    async def count_candidates(self, query: PoolQuery, *, also: PlayableFor | None = None) -> int:
        """Размер пула (``also`` — второй набор ассетов, ревил другого типа)."""
        value = await self._s.scalar(
            select(func.count()).select_from(catalog_items).where(*_candidate_filter(query, also))
        )
        return int(value or 0)

    async def candidates(
        self, query: PoolQuery, limit: int, *, also: PlayableFor | None = None
    ) -> list[MediaItemRef]:
        """``CandidatePort``: кандидаты пула в порядке ``id`` (детерминизм ``draw``)."""
        rows = (
            await self._s.execute(
                select(catalog_items)
                .where(*_candidate_filter(query, also))
                .order_by(catalog_items.c.media_item_id)
                .limit(limit)
            )
        ).mappings()
        return [_to_item(r).to_ref() for r in rows]

    async def has_assets(self, clue_type: str, params_hash: str) -> bool:
        """Есть ли в проекции хоть один каталожный набор под параметры загадки (профиль
        media есть)."""
        a = clue_assets.c
        found = await self._s.scalar(
            select(literal(1))
            .where(a.clue_type == clue_type, a.params_hash == params_hash, a.scope_id.is_(None))
            .limit(1)
        )
        return found is not None

    async def subjects(
        self,
        query: SubjectQuery,
        *,
        text_: str | None,
        limit: int,
        also: PlayableFor | None = None,
    ) -> list[SubjectRow]:
        """Персоны с ролью из ``query.roles`` и ≥ ``min_items`` играбельных единиц домена.

        Семантика — ``gv_testkit.game.FakeCandidatePort.subjects``: персона считается в
        своей роли, имя ищется подстрокой без учёта регистра, порядок — по числу единиц
        (убывание), имени и id. Индекс — GIN по ``person_ids`` (поиск по имени без
        trigram, лимит ≤ 20 — хвост этапа 2).
        """
        c = catalog_items.c
        elems = (
            func.jsonb_array_elements(c.persons).table_valued(column("value", JSONB)).lateral("p")
        )
        pid = elems.c.value["id"].astext
        role = elems.c.value["role"].astext
        name = elems.c.value["name"].astext
        conditions = _candidate_filter(
            PoolQuery(domain=query.domain, playable_for=query.playable_for), also
        )
        conditions.append(role.in_(sorted(query.roles)))
        if text_:
            conditions.append(name.ilike(_like(text_), escape="\\"))
        items = func.count(func.distinct(c.media_item_id))
        first_name = func.min(name.collate("C"))
        rows = await self._s.execute(
            select(pid, role, first_name, items)
            .select_from(catalog_items.join(elems, true()))
            .where(*conditions)
            .group_by(pid, role)
            .having(items >= query.min_items)
            .order_by(items.desc(), first_name, pid)
            .limit(limit)
        )
        return [SubjectRow(person_id=UUID(p), name=n, role=r, items=int(k)) for p, r, n, k in rows]

    async def search(self, *, q: str | None, domain: str | None, limit: int) -> list[CatalogItem]:
        """Поиск по подстроке названия (без учёта регистра)."""
        query = select(catalog_items)
        if domain is not None:
            query = query.where(catalog_items.c.domain == domain)
        if q:
            query = query.where(catalog_items.c.title.ilike(_like(q), escape="\\"))
        rows = (
            await self._s.execute(query.order_by(catalog_items.c.title).limit(limit))
        ).mappings()
        return [_to_item(r) for r in rows]


class SqlCandidatePort:
    """``CandidatePort`` ядра поверх проекции (ограничение — ``candidate_limit``).

    ``also`` — второй набор ассетов, без которого единица не играбельна (ревил другого
    типа, ADR 0026): ядро о нём не знает, фильтр добавляет адаптер.
    """

    def __init__(
        self, projection: SqlProjectionRepository, limit: int, also: PlayableFor | None = None
    ) -> None:
        self._projection = projection
        self._limit = limit
        self._also = also

    async def candidates(self, query: PoolQuery, limit: int) -> list[MediaItemRef]:
        """Кандидаты пула (не больше ``candidate_limit`` процесса)."""
        return await self._projection.candidates(query, min(limit, self._limit), also=self._also)


# ---------------------------------------------------------------- сборники
def _to_collection(row: RowMapping) -> CollectionProj:
    return CollectionProj(
        collection_id=row["collection_id"],
        version=row["version"],
        content_version=row["content_version"],
        domain=row["domain"],
        status=CollectionStatus(row["status"]),
        visibility=row["visibility"],
        author_id=row["author_id"],
        updated_at=row["updated_at"],
        tags=tuple(row["tags"] or ()),
    )


class SqlCollectionRepository:
    """Проекция сборников (``collection_proj``, ``collection_items_proj``, ADR 0028)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, collection_id: UUID, *, for_update: bool = False) -> CollectionProj | None:
        """Сборник (в том числе надгробие удалённого)."""
        query = select(collection_proj).where(collection_proj.c.collection_id == collection_id)
        if for_update:
            query = query.with_for_update()
        row = (await self._s.execute(query)).mappings().first()
        return _to_collection(row) if row else None

    async def save(self, proj: CollectionProj, items: Sequence[CollectionItem]) -> None:
        """Записать снимок сборника и заменить его состав."""
        values = {
            "version": proj.version,
            "content_version": proj.content_version,
            "domain": proj.domain,
            "status": str(proj.status),
            "visibility": proj.visibility,
            "author_id": proj.author_id,
            "updated_at": proj.updated_at,
            "tags": list(proj.tags),
        }
        await self._s.execute(
            pg_insert(collection_proj)
            .values(collection_id=proj.collection_id, **values)
            .on_conflict_do_update(index_elements=[collection_proj.c.collection_id], set_=values)
        )
        await self._s.execute(
            delete(collection_items_proj).where(
                collection_items_proj.c.collection_id == proj.collection_id
            )
        )
        if items:
            await self._s.execute(
                insert(collection_items_proj),
                [
                    {
                        "collection_id": proj.collection_id,
                        "position": i.position,
                        "media_item_id": i.media_item_id,
                    }
                    for i in items
                ],
            )

    async def total(self, collection_id: UUID) -> int:
        """Различных единиц сборника, активных в каталоге."""
        ci, c = collection_items_proj.c, catalog_items.c
        value = await self._s.scalar(
            select(func.count(func.distinct(ci.media_item_id)))
            .select_from(
                collection_items_proj.join(catalog_items, c.media_item_id == ci.media_item_id)
            )
            .where(ci.collection_id == collection_id, c.status == str(ItemStatus.ACTIVE))
        )
        return int(value or 0)

    async def items(
        self,
        collection_id: UUID,
        limit: int,
        *,
        playable_for: PlayableFor | None,
        also: PlayableFor | None = None,
    ) -> list[MediaItemRef]:
        """Единицы опубликованного сборника по ``position``: активные в каталоге и, при
        ``playable_for``, с готовым набором (со ``scope`` из требования — сначала свой
        набор сборника, иначе каталожный). Семантика —
        ``gv_testkit.game.FakeCollectionPort.matches``; ``also`` — второй набор (ревил
        другого типа, ADR 0026)."""
        ci, c, p = collection_items_proj.c, catalog_items.c, collection_proj.c
        conditions: list[Any] = [
            ci.collection_id == collection_id,
            p.status == str(CollectionStatus.PUBLISHED),
            c.status == str(ItemStatus.ACTIVE),
        ]
        conditions += [_ready_for(need) for need in (playable_for, also) if need is not None]
        rows = (
            await self._s.execute(
                select(catalog_items)
                .select_from(
                    collection_items_proj.join(
                        collection_proj, p.collection_id == ci.collection_id
                    ).join(catalog_items, c.media_item_id == ci.media_item_id)
                )
                .where(*conditions)
                .order_by(ci.position)
                .limit(limit)
            )
        ).mappings()
        return [_to_item(r).to_ref() for r in rows]


class SqlLadderRepository:
    """Проекция ключей ladder-ов (``ladders_proj``, ADR 0037)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, ladder: str, *, for_update: bool = False) -> LadderProj | None:
        """Ladder или ``None``."""
        query = select(ladders_proj).where(ladders_proj.c.ladder == ladder)
        if for_update:
            query = query.with_for_update()
        row = (await self._s.execute(query)).mappings().first()
        if row is None:
            return None
        return LadderProj(
            ladder=row["ladder"],
            domain=row["domain"],
            version=row["version"],
            updated_at=row["updated_at"],
        )

    async def save(self, proj: LadderProj) -> None:
        """Создать или заменить ladder."""
        values = {"domain": proj.domain, "version": proj.version, "updated_at": proj.updated_at}
        await self._s.execute(
            pg_insert(ladders_proj)
            .values(ladder=proj.ladder, **values)
            .on_conflict_do_update(
                index_elements=[ladders_proj.c.ladder],
                set_=values,
                # Порядок защищает версия и на уровне SQL: параллельная устаревшая запись
                # (гонка двух потребителей) не перезапишет более новую.
                where=ladders_proj.c.version < pg_insert(ladders_proj).excluded.version,
            )
        )


class SqlCollectionPort:
    """``CollectionPort`` ядра поверх проекции сборников (ограничение — ``candidate_limit``).

    ``also`` — второй набор ассетов ревила другого типа (ядро о нём не знает, как у
    ``SqlCandidatePort``); для него свой набор сборника тоже учитывается первым.
    """

    def __init__(
        self, collections: SqlCollectionRepository, limit: int, also: PlayableFor | None = None
    ) -> None:
        self._collections = collections
        self._limit = limit
        self._also = also

    async def items(
        self, collection_id: UUID, limit: int, *, playable_for: PlayableFor | None
    ) -> list[MediaItemRef]:
        """До ``limit`` единиц сборника в его порядке."""
        also = self._also
        if also is not None and playable_for is not None:
            also = PlayableFor(also.clue_type, also.params_hash, scope=playable_for.scope)
        return await self._collections.items(
            collection_id, min(limit, self._limit), playable_for=playable_for, also=also
        )


# ---------------------------------------------------------------- сессии
def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _state_json(s: RoundState) -> dict[str, Any]:
    return {
        "level": s.level,
        "attempts_on_level": s.attempts_on_level,
        "outcome": str(s.outcome) if s.outcome else None,
        "partial_awarded_level": s.partial_awarded_level,
        "guesses": [
            {
                "kind": str(g.kind),
                "level": g.level,
                "verdict": str(g.verdict) if g.verdict else None,
                "display": g.display,
            }
            for g in s.guesses
        ],
    }


def _state_of(raw: dict[str, Any]) -> RoundState:
    return RoundState(
        level=raw["level"],
        attempts_on_level=raw["attempts_on_level"],
        outcome=RoundResult(raw["outcome"]) if raw["outcome"] else None,
        partial_awarded_level=raw["partial_awarded_level"],
        guesses=tuple(
            Guess(
                kind=GuessKind(g["kind"]),
                level=g["level"],
                verdict=MatchVerdict(g["verdict"]) if g["verdict"] else None,
                display=g["display"],
            )
            for g in raw["guesses"]
        ),
    )


def _asset_json(asset: AssetRef | None) -> dict[str, Any] | None:
    if asset is None:
        return None
    return {"asset_set_id": str(asset.asset_set_id), "levels_count": asset.levels_count}


def _asset_of(raw: dict[str, Any] | None) -> AssetRef | None:
    if not raw:
        return None
    return AssetRef(asset_set_id=UUID(raw["asset_set_id"]), levels_count=raw["levels_count"])


def _option_json(o: RoundOption) -> dict[str, Any]:
    return {
        "id": o.id,
        "label_key": o.label_key,
        "media_item_id": str(o.media_item_id) if o.media_item_id else None,
    }


def _option_of(raw: dict[str, Any]) -> RoundOption:
    item = raw.get("media_item_id")
    return RoundOption(
        id=raw["id"], label_key=raw.get("label_key"), media_item_id=UUID(item) if item else None
    )


def _round_json(r: Round) -> dict[str, Any]:
    return {
        "index": r.index,
        "media_item_id": str(r.media_item_id),
        "asset": _asset_json(r.asset),
        "levels_count": r.levels_count,
        "max_points": r.max_points,
        "points": r.points,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "state": _state_json(r.state),
        "shown": [
            {"media_item_id": str(x.media_item_id), "asset": _asset_json(x.asset)} for x in r.shown
        ],
        "options": [_option_json(o) for o in r.options],
        "choice": r.choice,
        "stage": r.stage,
        "reveal_asset": _asset_json(r.reveal_asset),
        "artwork_asset": _asset_json(r.artwork_asset),
    }


def _round_of(r: dict[str, Any]) -> Round:
    """Раунд из ``state``; поля этапа 3a у снимков v1 отсутствуют — значения по умолчанию."""
    return Round(
        index=r["index"],
        media_item_id=UUID(r["media_item_id"]),
        asset=_asset_of(r["asset"]),
        levels_count=r["levels_count"],
        max_points=r["max_points"],
        state=_state_of(r["state"]),
        points=r["points"],
        started_at=_dt(r["started_at"]),
        finished_at=_dt(r["finished_at"]),
        shown=tuple(
            ShownItem(UUID(x["media_item_id"]), _asset_of(x.get("asset")))
            for x in r.get("shown", ())
        ),
        options=tuple(_option_of(o) for o in r.get("options", ())),
        choice=r.get("choice"),
        stage=r.get("stage"),
        reveal_asset=_asset_of(r.get("reveal_asset")),
        artwork_asset=_asset_of(r.get("artwork_asset")),
    )


def _planned_json(p: PlannedRound) -> dict[str, Any]:
    return {
        "target": str(p.target),
        "shown": [str(x) for x in p.shown],
        "options": [_option_json(o) for o in p.options],
    }


def _planned_of(raw: dict[str, Any]) -> PlannedRound:
    return PlannedRound(
        target=UUID(raw["target"]),
        shown=tuple(UUID(x) for x in raw.get("shown", ())),
        options=tuple(_option_of(o) for o in raw.get("options", ())),
    )


def session_state_json(s: GameSession) -> dict[str, Any]:
    """``game_sessions.state`` схемы ``STATE_SCHEMA`` (план скрыт от клиента, D4).

    ``collection`` (этап 3b) — необязательное поле той же схемы: без него сессия не по
    сборнику.
    """
    state: dict[str, Any] = {
        "state_schema": STATE_SCHEMA,
        "rounds": [_round_json(r) for r in s.rounds],
        "plan": [_planned_json(p) for p in s.plan],
        "progressive": s.progressive,
        "round_max": s.round_max,
    }
    if s.collection_id is not None:
        state["collection"] = {
            "id": str(s.collection_id),
            "version": s.collection_version,
            "tags": list(s.collection_tags),
        }
    return state


@dataclass(frozen=True, slots=True)
class SessionState:
    """Разобранный ``game_sessions.state`` любой поддерживаемой версии схемы."""

    rounds: list[Round]
    plan: list[PlannedRound]
    progressive: bool
    round_max: int | None
    collection_id: UUID | None = None
    collection_version: int | None = None
    collection_tags: tuple[str, ...] = ()


def session_state_of(raw: dict[str, Any]) -> SessionState:
    """Разобрать ``state``: без ``state_schema`` — v1 этапа 2 (все раунды выданы, плана нет).

    Raises:
        ValueError: неизвестная (более новая) версия схемы.
    """
    schema = int(raw.get("state_schema", 1))
    if schema > STATE_SCHEMA:
        raise ValueError(f"неизвестная версия схемы состояния сессии: {schema}")
    rounds = [_round_of(r) for r in raw["rounds"]]
    collection = raw.get("collection")
    return SessionState(
        rounds=rounds,
        plan=[_planned_of(p) for p in raw.get("plan", ())],
        progressive=bool(raw.get("progressive", False)),
        round_max=raw.get("round_max"),
        collection_id=UUID(collection["id"]) if collection else None,
        collection_version=int(collection["version"]) if collection else None,
        collection_tags=tuple(collection.get("tags") or ()) if collection else (),
    )


def _session_values(s: GameSession) -> dict[str, Any]:
    return {
        "status": str(s.status),
        "score": s.score,
        "max_score": s.max_score,
        "version": s.version,
        "state": session_state_json(s),
        "finished_at": s.finished_at,
        "last_move_at": s.last_move_at,
    }


class SqlSessionRepository:
    """Сессии."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @staticmethod
    def _to_session(row: RowMapping) -> GameSession:
        state = session_state_of(row["state"])
        return GameSession(
            row["id"],
            player=PlayerRef(PlayerKind(row["player_kind"]), row["player_id"]),
            mode_key=row["mode_key"],
            mode_version=row["mode_version"],
            domain=row["domain"],
            source_type=row["source_type"],
            clue_type=row["clue_type"],
            day=row["day"],
            preset_id=row["preset_id"],
            seed=bytes(row["seed"]),
            status=SessionStatus(row["status"]),
            rounds=state.rounds,
            started_at=row["started_at"],
            last_move_at=row["last_move_at"],
            idle_timeout_s=row["idle_timeout_s"],
            finished_at=row["finished_at"],
            version=row["version"],
            plan=state.plan,
            progressive=state.progressive,
            round_max=state.round_max,
            collection_id=state.collection_id,
            collection_version=state.collection_version,
            collection_tags=state.collection_tags,
        )

    async def get(self, session_id: UUID, *, for_update: bool = False) -> GameSession | None:
        """Сессия."""
        query = select(game_sessions).where(game_sessions.c.id == session_id)
        if for_update:
            query = query.with_for_update()
        row = (await self._s.execute(query)).mappings().first()
        return self._to_session(row) if row else None

    async def add(self, session: GameSession) -> bool:
        """Вставить; ``False`` — попытка дня уже есть (частичный unique)."""
        result = await self._s.execute(
            pg_insert(game_sessions)
            .values(
                id=session.id,
                player_kind=str(session.player.kind),
                player_id=session.player.id,
                mode_key=session.mode_key,
                mode_version=session.mode_version,
                domain=session.domain,
                source_type=session.source_type,
                clue_type=session.clue_type,
                day=session.day,
                preset_id=session.preset_id,
                seed=session.seed,
                idle_timeout_s=session.idle_timeout_s,
                started_at=session.started_at,
                **_session_values(session),
            )
            .on_conflict_do_nothing(
                index_elements=["mode_key", "day", "player_kind", "player_id"],
                index_where=text("day IS NOT NULL"),
            )
            .returning(game_sessions.c.id)
        )
        return result.first() is not None

    async def save(self, session: GameSession) -> None:
        """Состояние сессии."""
        await self._s.execute(
            update(game_sessions)
            .where(game_sessions.c.id == session.id)
            .values(**_session_values(session))
        )

    async def find_daily(self, mode_key: str, day: date, player: PlayerRef) -> GameSession | None:
        """Попытка дня игрока."""
        if player.id is None:
            return None
        row = (
            (
                await self._s.execute(
                    select(game_sessions).where(
                        game_sessions.c.mode_key == mode_key,
                        game_sessions.c.day == day,
                        game_sessions.c.player_kind == str(player.kind),
                        game_sessions.c.player_id == player.id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return self._to_session(row) if row else None

    async def stale_ids(self, now: datetime, limit: int) -> list[UUID]:
        """Активные сессии без ходов дольше ``idle_timeout_s``."""
        g = game_sessions.c
        rows = await self._s.execute(
            select(g.id)
            .where(
                g.status == str(SessionStatus.ACTIVE),
                g.last_move_at + func.make_interval(0, 0, 0, 0, 0, 0, g.idle_timeout_s) < now,
            )
            .order_by(g.last_move_at)
            .limit(limit)
        )
        return [r for (r,) in rows]

    async def forget_player(self, user_id: UUID) -> int:
        """Обнулить ``player_id`` пользователя."""
        result = await self._s.execute(
            update(game_sessions)
            .where(
                game_sessions.c.player_kind == str(PlayerKind.USER),
                game_sessions.c.player_id == user_id,
            )
            .values(player_id=None)
        )
        return int(getattr(result, "rowcount", 0) or 0)


class SqlDailyStatsRepository:
    """Статистика раундов дейлика."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(self, mode_key: str, day: date, rounds: Sequence[tuple[int, str]]) -> None:
        """Учесть итоги раундов (``void`` пропускается)."""
        rows = [
            {
                "mode_key": mode_key,
                "day": day,
                "idx": index,
                "played": 1,
                "exact": int(outcome == RoundResult.EXACT),
                "partial": int(outcome == RoundResult.PARTIAL),
            }
            for index, outcome in rounds
            if outcome != RoundResult.VOID
        ]
        if not rows:
            return
        stmt = pg_insert(daily_round_stats).values(rows)
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    daily_round_stats.c.mode_key,
                    daily_round_stats.c.day,
                    daily_round_stats.c.idx,
                ],
                set_={
                    "played": daily_round_stats.c.played + stmt.excluded.played,
                    "exact": daily_round_stats.c.exact + stmt.excluded.exact,
                    "partial": daily_round_stats.c.partial + stmt.excluded.partial,
                },
            )
        )

    async def get(self, mode_key: str, day: date) -> dict[int, tuple[int, int]]:
        """``{index: (played, exact)}``."""
        rows = await self._s.execute(
            select(
                daily_round_stats.c.idx, daily_round_stats.c.played, daily_round_stats.c.exact
            ).where(daily_round_stats.c.mode_key == mode_key, daily_round_stats.c.day == day)
        )
        return {idx: (played, exact) for idx, played, exact in rows}


# ---------------------------------------------------------------- этап 3a: рекорды, сетка
class SqlRecordsRepository:
    """Личные рекорды и гистограмма результатов режима."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save_result(
        self, user_id: UUID, mode_key: str, score: int, *, session_id: UUID, at: datetime
    ) -> bool:
        """Гистограмма +1; рекорд обновляется, только если результат лучше прежнего."""
        hist = pg_insert(score_histogram).values(mode_key=mode_key, score=score, sessions=1)
        await self._s.execute(
            hist.on_conflict_do_update(
                index_elements=[score_histogram.c.mode_key, score_histogram.c.score],
                set_={"sessions": score_histogram.c.sessions + 1},
            )
        )
        stmt = pg_insert(player_records).values(
            user_id=user_id, mode_key=mode_key, best=score, session_id=session_id, achieved_at=at
        )
        result = await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[player_records.c.user_id, player_records.c.mode_key],
                set_={
                    "best": stmt.excluded.best,
                    "session_id": stmt.excluded.session_id,
                    "achieved_at": stmt.excluded.achieved_at,
                },
                where=player_records.c.best < stmt.excluded.best,
            ).returning(player_records.c.best)
        )
        return result.first() is not None

    @staticmethod
    def _to_record(row: RowMapping) -> PlayerRecord:
        return PlayerRecord(
            mode_key=row["mode_key"],
            best=row["best"],
            session_id=row["session_id"],
            achieved_at=row["achieved_at"],
        )

    async def get(self, user_id: UUID, mode_key: str) -> PlayerRecord | None:
        """Рекорд пользователя в режиме."""
        row = (
            (
                await self._s.execute(
                    select(player_records).where(
                        player_records.c.user_id == user_id, player_records.c.mode_key == mode_key
                    )
                )
            )
            .mappings()
            .first()
        )
        return self._to_record(row) if row else None

    async def for_user(self, user_id: UUID, mode_key: str | None) -> list[PlayerRecord]:
        """Рекорды пользователя по ключу режима."""
        query = select(player_records).where(player_records.c.user_id == user_id)
        if mode_key is not None:
            query = query.where(player_records.c.mode_key == mode_key)
        rows = (await self._s.execute(query.order_by(player_records.c.mode_key))).mappings()
        return [self._to_record(r) for r in rows]

    async def percentile(self, mode_key: str, score: int) -> float | None:
        """Доля результатов строго ниже ``score`` среди всех результатов режима, %."""
        h = score_histogram.c
        below = func.coalesce(func.sum(h.sessions).filter(h.score < score), 0)
        total = func.coalesce(func.sum(h.sessions), 0)
        row = (await self._s.execute(select(below, total).where(h.mode_key == mode_key))).one()
        lower, all_ = int(row[0]), int(row[1])
        return round(100.0 * lower / all_, 1) if all_ else None


class SqlBracketStatsRepository:
    """Статистика выборов сетки по паре ``(winner, loser)``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add_pick(
        self, mode_key: str, period_start: date, winner_id: UUID, loser_id: UUID
    ) -> None:
        """+1 к паре (upsert в транзакции хода)."""
        stmt = pg_insert(bracket_pick_stats).values(
            mode_key=mode_key,
            period_start=period_start,
            winner_id=winner_id,
            loser_id=loser_id,
            picks=1,
        )
        b = bracket_pick_stats.c
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[b.mode_key, b.period_start, b.winner_id, b.loser_id],
                set_={"picks": b.picks + 1},
            )
        )

    async def agree_pct(
        self, mode_key: str, period_start: date, winner_id: UUID, loser_id: UUID
    ) -> float | None:
        """Доля выборов ``winner`` среди выборов этой пары в обе стороны, %."""
        b = bracket_pick_stats.c
        rows = await self._s.execute(
            select(b.winner_id, b.picks).where(
                b.mode_key == mode_key,
                b.period_start == period_start,
                or_(
                    and_(b.winner_id == winner_id, b.loser_id == loser_id),
                    and_(b.winner_id == loser_id, b.loser_id == winner_id),
                ),
            )
        )
        counts = {w: int(n) for w, n in rows}
        total = sum(counts.values())
        return round(100.0 * counts.get(winner_id, 0) / total, 1) if total else None


class SqlAssetRequestRepository:
    """Запросы ассетов (ADR 0025)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @staticmethod
    def _to_request(row: RowMapping) -> AssetRequest:
        return AssetRequest(
            row["request_id"],
            mode_key=row["mode_key"],
            draft_revision=row["draft_revision"],
            domain=domain_of(row["mode_key"]),
            clue_type=row["clue_type"],
            params=dict(row["params"]),
            params_hash=row["params_hash"],
            status=AssetRequestStatus(row["status"]),
            actor_id=row["actor_id"],
            reason=row["reason"],
            requested_at=row["requested_at"],
        )

    async def pending(self, clue_type: str, params_hash: str) -> AssetRequest | None:
        """Ожидающий запрос под эти параметры."""
        a = asset_requests.c
        row = (
            (
                await self._s.execute(
                    select(asset_requests).where(
                        a.clue_type == clue_type,
                        a.params_hash == params_hash,
                        a.status == str(AssetRequestStatus.PENDING),
                    )
                )
            )
            .mappings()
            .first()
        )
        return self._to_request(row) if row else None

    async def add(self, request: AssetRequest) -> bool:
        """Вставить; ``False`` — ожидающий запрос под хэш уже есть."""
        result = await self._s.execute(
            pg_insert(asset_requests)
            .values(
                request_id=request.id,
                mode_key=request.mode_key,
                draft_revision=request.draft_revision,
                clue_type=request.clue_type,
                params_hash=request.params_hash,
                params=request.params,
                status=str(request.status),
                actor_id=request.actor_id,
                reason=request.reason,
                requested_at=request.requested_at,
            )
            .on_conflict_do_nothing(
                index_elements=["clue_type", "params_hash"],
                index_where=text("status = 'pending'"),
            )
            .returning(asset_requests.c.request_id)
        )
        return result.first() is not None

    async def for_mode(self, mode_key: str) -> list[AssetRequest]:
        """Запросы режима, новые первыми."""
        rows = (
            await self._s.execute(
                select(asset_requests)
                .where(asset_requests.c.mode_key == mode_key)
                .order_by(asset_requests.c.requested_at.desc(), asset_requests.c.request_id.desc())
            )
        ).mappings()
        return [self._to_request(r) for r in rows]

    async def mark_sent(self, clue_type: str, params_hash: str) -> int:
        """Ожидающие запросы под параметры → ``sent``."""
        a = asset_requests.c
        result = await self._s.execute(
            update(asset_requests)
            .where(
                a.clue_type == clue_type,
                a.params_hash == params_hash,
                a.status == str(AssetRequestStatus.PENDING),
            )
            .values(status=str(AssetRequestStatus.SENT))
        )
        return int(getattr(result, "rowcount", 0) or 0)


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


# ---------------------------------------------------------------- Unit of Work
class SqlGameUnitOfWork:
    """Транзакция use-case с репозиториями и записью событий в outbox.

    Два режима: своя транзакция (API, задачи) или SAVEPOINT в транзакции consumer, где уже
    стоит отметка inbox (коммит всей транзакции делает ``EventProcessor``).
    """

    modes: ModeConfigRepository
    daily_sets: DailySetRepository
    projection: ProjectionRepository
    candidates: CandidatePort
    sessions: SessionRepository
    stats: DailyStatsRepository
    idempotency: IdempotencyStore
    audit: AdminAuditLog
    records: RecordsRepository
    bracket_stats: BracketStatsRepository
    asset_requests: AssetRequestRepository
    collections: CollectionRepository
    ladders: LadderRepository

    def __init__(
        self,
        *,
        producer: str,
        clock: Clock,
        candidate_limit: int,
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
        self._candidate_limit = candidate_limit
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
        self.modes = SqlModeConfigRepository(s)
        self.daily_sets = SqlDailySetRepository(s, self._clock)
        projection = SqlProjectionRepository(s)
        self.projection = projection
        self.candidates = SqlCandidatePort(projection, self._candidate_limit)
        self.sessions = SqlSessionRepository(s)
        self.stats = SqlDailyStatsRepository(s)
        self.idempotency = SqlIdempotencyStore(s, idempotency_keys, self._clock)
        self.audit = SqlAdminAuditLog(s)
        self.records = SqlRecordsRepository(s)
        self.bracket_stats = SqlBracketStatsRepository(s)
        self.asset_requests = SqlAssetRequestRepository(s)
        self._collections = SqlCollectionRepository(s)
        self.collections = self._collections
        self.ladders = SqlLadderRepository(s)
        self._projection = projection
        return self

    def candidates_for(self, also: PlayableFor | None) -> CandidatePort:
        """``CandidatePort`` с требованием второго набора ассетов (ревил, ADR 0026)."""
        if also is None:
            return self.candidates
        return SqlCandidatePort(self._projection, self._candidate_limit, also)

    def collection_port(self, also: PlayableFor | None) -> CollectionPort:
        """``CollectionPort`` ядра (ADR 0028) с требованием второго набора ассетов."""
        return SqlCollectionPort(self._collections, self._candidate_limit, also)

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
