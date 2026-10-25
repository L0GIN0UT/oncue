"""Фикстуры тестов progression. Инфраструктура и фейки — из ``gv_testkit``.

Здесь — сборка сервиса на реальном Postgres (``progression``): HTTP-клиент, доставка событий
через настоящий ``EventProcessor`` (inbox, транзакция), публикация конфига из примера контракта
и построители входных событий (game, match, rating).
"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

import httpx
import pytest
from progression_service.application.accrual.common import published_config
from progression_service.application.accrual.posting import Poster
from progression_service.infrastructure.container import ProgressionContainer, container_for
from progression_service.infrastructure.db.tables import (
    TABLES,
    balances,
    config_versions,
    currency_ledger,
    progress,
    xp_ledger,
)
from progression_service.infrastructure.db.tables_shop import items
from progression_service.main import DEFINITION
from progression_service.settings import ProgressionSettings, S3Settings, Settings
from pydantic import SecretStr
from sqlalchemy import func, select, text, update

from gv_core.ids import uuid7
from gv_messaging import EventEnvelope, EventProcessor, Inbox, Outcome
from gv_messaging.retry import RetryPolicy
from gv_service import Infrastructure, JobResult, create_app
from gv_testkit import (
    FakeClock,
    FakeDeadLetterSink,
    FakeRandom,
    InfraProvider,
    TestTokenIssuer,
    apply_migrations,
    load_example,
    outbox_envelopes,
    run_periodic_job,
    service_client,
)

EXAMPLE_CONFIG = (
    Path(__file__).resolve().parents[3]
    / "contracts"
    / "progression"
    / "examples"
    / "progression_config.v1.json"
)
# Пятница 12:00 UTC = 15:00 в Europe/Moscow (зона примера конфига).
START = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
ADMIN = UUID(int=1)
EVENTS_MANAGE = "progression.events.manage"
EVENTS_PUBLISH = "progression.events.publish"


class EventKit:
    """Построители определений сезонных событий (5d): ``progression.ev.*``."""

    MANAGE = EVENTS_MANAGE
    PUBLISH = EVENTS_PUBLISH

    @staticmethod
    def find(
        key: str,
        number: int,
        trigger: dict[str, Any] | None = None,
        *,
        final: bool = False,
        xp: int = 10,
        verses: int = 5,
    ) -> dict[str, Any]:
        """Находка определения (по умолчанию - спрятанный элемент на главной)."""
        return {
            "key": key,
            "number": number,
            "title": f"Находка {key}",
            "diary_before": f"до {key}",
            "diary_after": f"после {key}",
            "x_pct": 10.5,
            "y_pct": 20,
            "reward": {"xp": xp, "verses": verses},
            "final": final,
            "trigger": trigger or {"type": "ui_spot", "page_key": "home"},
        }

    @staticmethod
    def trigger(
        metric: str, target: int = 1, period: str = "event", **params: Any
    ) -> dict[str, Any]:
        """Серверный триггер находки."""
        trigger: dict[str, Any] = {
            "type": "server",
            "metric": metric,
            "target": target,
            "period": period,
        }
        if params:
            trigger["params"] = params
        return trigger

    @staticmethod
    def body(*finds: dict[str, Any], item_key: str | None = None) -> dict[str, Any]:
        """Тело определения события."""
        return {
            "title": "Осень",
            "description": "Листопад",
            "theme_key": "autumn",
            "finds": list(finds),
            "reward": {"item_key": item_key},
        }


def config_body(**patch: Any) -> dict[str, Any]:
    """Тело конфига из примера контракта; ``patch`` — вложенные правки через ``__``."""
    raw: dict[str, Any] = copy.deepcopy(json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8")))
    for dotted, value in patch.items():
        target = raw
        *head, last = dotted.split("__")
        for part in head:
            target = target[part]
        if value is ...:
            target.pop(last, None)  # `...` - убрать необязательное поле
        else:
            target[last] = value
    return raw


class RiggedRandom(FakeRandom):
    """``Random`` колеса: ``randrange`` всегда возвращает ``roll`` (заданный сектор)."""

    def __init__(self, roll: int) -> None:
        super().__init__()
        self._roll = roll

    def randrange(self, start: int, stop: int | None = None, step: int = 1) -> int:
        return self._roll


def _body(rewards: bool, patch: dict[str, Any]) -> dict[str, Any]:
    """Тело для публикации: без ``rewards`` пул заданий пуст (тесты начисления P1 не видят наград
    заданий в сумме опыта); ``rewards=True`` - задания из примера контракта (тесты P2)."""
    body = config_body(**patch)
    if not rewards:
        body["quests"].update(items=[], daily_count=0, weekly_count=0)
    return body


def exact_round(index: int, points: int, outcome: str = "exact") -> dict[str, Any]:
    """Раунд ``GameFinished``."""
    return {
        "index": index,
        "media_item_id": str(uuid7()),
        "outcome": outcome,
        "level": 1 if outcome == "exact" else None,
        "points": points,
        "elapsed_ms": 1000,
    }


@dataclass
class ProgressionService:
    """Сервис на реальном Postgres."""

    client: httpx.AsyncClient
    infra: Infrastructure
    container: ProgressionContainer
    processor: EventProcessor
    dead_letters: FakeDeadLetterSink
    issuer: TestTokenIssuer
    clock: FakeClock
    versions: list[int] = field(default_factory=list)
    ev: ClassVar[type[EventKit]] = EventKit

    # ------------------------------------------------------------------ конфиг
    async def publish_config(
        self,
        version: int = 1,
        *,
        at: datetime | None = None,
        rewards: bool = False,
        **patch: Any,
    ) -> int:
        """Опубликовать версию конфига (предыдущая действующая уходит в архив).

        ``at`` — момент публикации (``published_at``); по умолчанию — часы сервиса.
        """
        assert self.infra.sessions is not None
        published_at = at or self.clock.now()
        async with self.infra.sessions() as session, session.begin():
            await session.execute(
                update(config_versions)
                .where(config_versions.c.status == "published")
                .values(status="archived")
            )
            await session.execute(
                config_versions.insert().values(
                    version=version,
                    status="published",
                    body=_body(rewards, patch),
                    created_by=ADMIN,
                    created_at=self.clock.now(),
                    published_at=published_at,
                )
            )
        self.versions.append(version)
        return version

    # ------------------------------------------------------------------ доставка и чтение
    async def deliver(self, envelope: EventEnvelope) -> Outcome:
        return await self.processor.process(envelope.to_bytes(), source="test")

    async def run_job(self, name: str) -> JobResult:
        return await run_periodic_job(DEFINITION, name, self.infra)

    async def outbox(self, event_type: str | None = None) -> list[EventEnvelope]:
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            events = await outbox_envelopes(session, TABLES.outbox)
        return [e for e in events if event_type is None or e.event_type == event_type]

    async def scalar(self, stmt: Any) -> Any:
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            return await session.scalar(stmt)

    async def xp_total(self, user_id: UUID) -> int:
        """Сумма журнала опыта игрока."""
        return int(
            await self.scalar(
                select(func.coalesce(func.sum(xp_ledger.c.amount), 0)).where(
                    xp_ledger.c.user_id == user_id
                )
            )
        )

    async def xp_rows(self, user_id: UUID | None = None) -> int:
        stmt = select(func.count()).select_from(xp_ledger)
        if user_id is not None:
            stmt = stmt.where(xp_ledger.c.user_id == user_id)
        return int(await self.scalar(stmt))

    async def verse_rows(self, user_id: UUID | None = None) -> int:
        stmt = select(func.count()).select_from(currency_ledger)
        if user_id is not None:
            stmt = stmt.where(currency_ledger.c.user_id == user_id)
        return int(await self.scalar(stmt))

    async def mark_rows(self, user_id: UUID | None = None) -> int:
        """Нулевые отметки предметов (``accrual_marks``)."""
        stmt = "SELECT count(*) FROM accrual_marks"
        params: dict[str, Any] = {}
        if user_id is not None:
            stmt += " WHERE user_id = :u"
            params["u"] = user_id
        return int(await self.scalar(text(stmt).bindparams(**params)))

    async def balance(self, user_id: UUID, bucket: str = "earned") -> int:
        value = await self.scalar(
            select(balances.c.amount).where(
                balances.c.user_id == user_id, balances.c.bucket == bucket
            )
        )
        return int(value or 0)

    async def level(self, user_id: UUID) -> int:
        return int(
            await self.scalar(select(progress.c.level).where(progress.c.user_id == user_id)) or 0
        )

    def user(self, user_id: UUID | None = None) -> dict[str, str]:
        token = self.issuer.issue(user_id=user_id or uuid7())
        return {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------ события
    def game_finished(
        self,
        user_id: UUID | None,
        points: list[int],
        *,
        day: date | None = None,
        finished_at: datetime | None = None,
        session_id: UUID | None = None,
        rounds: list[dict[str, Any]] | None = None,
    ) -> EventEnvelope:
        """``game.GameFinished`` (``user_id = None`` — гость)."""
        base = load_example("game.GameFinished")
        sid = session_id or uuid7()
        payload = {
            **base.payload,
            "session_id": str(sid),
            "user_id": None if user_id is None else str(user_id),
            "is_guest": user_id is None,
            "day": None if day is None else day.isoformat(),
            "finished_at": (finished_at or self.clock.now()).isoformat(),
            "rounds": rounds or [exact_round(i, p) for i, p in enumerate(points)],
        }
        return base.model_copy(update={"aggregate_id": str(sid), "payload": payload})

    def daily_completed(
        self, user_id: UUID, day: date, *, domain: str = "music", session_id: UUID | None = None
    ) -> EventEnvelope:
        """``game.DailyCompleted``."""
        base = load_example("game.DailyCompleted")
        sid = session_id or uuid7()
        payload = {
            **base.payload,
            "session_id": str(sid),
            "user_id": str(user_id),
            "domain": domain,
            "day": day.isoformat(),
        }
        return base.model_copy(update={"aggregate_id": str(sid), "payload": payload})

    def match_finished(
        self,
        players: list[dict[str, Any]],
        *,
        format: str = "duel",  # noqa: A002 — поле контракта
        rounds: list[list[tuple[UUID, int]]] | None = None,
        aborted: bool = False,
        finished_at: datetime | None = None,
        match_id: UUID | None = None,
    ) -> EventEnvelope:
        """``match.MatchFinished``; игрок — ``{user_id, place, outcome, finish}``."""
        base = load_example("match.MatchFinished")
        mid = match_id or uuid7()
        body = [
            {
                "index": i,
                "media_item_id": str(uuid7()),
                "results": [
                    {
                        "user_id": str(u),
                        "level": 1 if s else None,
                        "partial": False,
                        "score": s,
                        "elapsed_ms": 500,
                    }
                    for u, s in rnd
                ],
            }
            for i, rnd in enumerate(rounds or [])
        ]
        payload = {
            **base.payload,
            "match_id": str(mid),
            "format": format,
            "aborted": aborted,
            "players": [
                {
                    "user_id": str(p["user_id"]),
                    "slot": i,
                    "score": 10,
                    "place": p.get("place", i + 1),
                    "outcome": p.get("outcome"),
                    "finish": p.get("finish", "completed"),
                }
                for i, p in enumerate(players)
            ],
            "rounds": body,
            "finished_at": (finished_at or self.clock.now()).isoformat(),
        }
        return base.model_copy(update={"aggregate_id": str(mid), "payload": payload})

    def duel(
        self,
        winner: UUID,
        loser: UUID,
        *,
        winner_rounds: int = 2,
        loser_rounds: int = 1,
        **kwargs: Any,
    ) -> EventEnvelope:
        """Дуэль: ``winner`` выигрывает ``winner_rounds`` раундов, ``loser`` — ``loser_rounds``."""
        rounds = [[(winner, 10), (loser, 0)]] * winner_rounds + [
            [(winner, 0), (loser, 10)]
        ] * loser_rounds
        return self.match_finished(
            [
                {"user_id": winner, "place": 1, "outcome": "win"},
                {"user_id": loser, "place": 2, "outcome": "loss"},
            ],
            rounds=rounds,
            **kwargs,
        )

    def weekly_top(
        self, places: list[tuple[UUID, int]], *, ladder: str = "music", week: date | None = None
    ) -> EventEnvelope:
        """``rating.WeeklyTopFinalized``."""
        base = load_example("rating.WeeklyTopFinalized")
        week_start = week or date(2026, 10, 19)
        payload = {
            **base.payload,
            "ladder": ladder,
            "week_start": week_start.isoformat(),
            "top": [{"user_id": str(u), "delta": 10, "place": p} for u, p in places],
        }
        return base.model_copy(
            update={"aggregate_id": f"{ladder}:{week_start.isoformat()}", "payload": payload}
        )

    def collection_published(
        self, author: UUID | None, *, collection_id: UUID | None = None, status: str = "published"
    ) -> EventEnvelope:
        """``collections.CollectionPublished`` (новый ``event_id`` на каждый вызов)."""
        base = load_example("collections.CollectionPublished")
        cid = collection_id or uuid7()
        payload = {
            **base.payload,
            "collection_id": str(cid),
            "author_id": None if author is None else str(author),
            "status": status,
        }
        return base.model_copy(update={"aggregate_id": str(cid), "payload": payload})

    # ------------------------------------------------------------------ HTTP и колесо
    async def get(self, path: str, user: UUID) -> httpx.Response:
        return await self.client.get(path, headers=self.user(user))

    async def spin(self, user: UUID, **kwargs: Any) -> httpx.Response:
        return await self.client.post("/v1/wheel/spin", headers=self.user(user), **kwargs)

    def rig_wheel(self, sector_key: str, *, config: dict[str, Any] | None = None) -> None:
        """Следующие спины выпадают на сектор ``sector_key`` (порт ``Random`` подменён)."""
        sectors = (config or config_body())["wheel"]["sectors"]
        start = 0
        for sector in sectors:
            if sector["key"] == sector_key:
                self.container.random = RiggedRandom(start)
                return
            start += sector["weight"]
        raise KeyError(sector_key)

    async def rows(self, table: str, user: UUID | None = None, **where: Any) -> list[Any]:
        """Строки таблицы (чтение для проверок): ``where`` - равенства по колонкам."""
        assert self.infra.sessions is not None
        sql = f"SELECT * FROM {table} WHERE true"  # noqa: S608 - имя таблицы из тестов
        params: dict[str, Any] = {}
        if user is not None:
            sql += " AND user_id = :user_id"
            params["user_id"] = user
        for column, value in where.items():
            sql += f" AND {column} = :{column}"
            params[column] = value
        async with self.infra.sessions() as session:
            return list((await session.execute(text(sql), params)).all())

    # ------------------------------------------------------------------ магазин (5b)
    async def add_item(
        self,
        key: str = "frame_gold",
        slot: str = "frame",
        rarity: str = "rare",
        *,
        price: int | None = 30,
        level_required: int | None = None,
        active: bool = True,
        media_kind: str = "static",
        event_key: str | None = None,
    ) -> UUID:
        """Предмет в таблице ``items`` (картинка-заглушка у активного).

        ``event_key`` - предмет награды события: без цены и уровня.
        """
        if event_key is not None:
            price, level_required = None, None
        assert self.infra.sessions is not None
        item_id = uuid7()
        async with self.infra.sessions() as session, session.begin():
            await session.execute(
                items.insert().values(
                    id=item_id,
                    key=key,
                    slot=slot,
                    rarity=rarity,
                    title=key,
                    media_kind=media_kind,
                    asset_key=f"c/{uuid7().hex[-32:]}.webp" if active else None,
                    poster_key=None,
                    level_required=level_required,
                    price=price,
                    active=active,
                    event_key=event_key,
                    created_at=self.clock.now(),
                    updated_at=self.clock.now(),
                )
            )
        return item_id

    async def grant_verses(self, user_id: UUID, amount: int) -> None:
        """Начислить Версы настоящим путём ledger (нужна опубликованная версия конфига)."""
        async with self.container.uow_factory()() as uow:
            published = await published_config(uow)
            poster = Poster(uow, published, self.clock.now())
            await poster.verses(
                user_id, op_id=f"test:{uuid7()}", amount=amount, reason="test", source_ref="test"
            )
            await uow.commit()

    async def set_level(self, user_id: UUID, level: int) -> None:
        """Уровень игрока (строка прогресса создаётся при отсутствии)."""
        async with self.container.uow_factory()() as uow:
            published = await published_config(uow)
            row = await Poster(uow, published, self.clock.now()).lock_user(user_id)
            row.level = level
            await uow.progress.save(row)
            await uow.commit()

    async def purchase(
        self, user_id: UUID, item_id: UUID, key: str | None = None
    ) -> httpx.Response:
        """``POST /v1/shop/items/{id}/purchase``."""
        headers = {**self.user(user_id), "Idempotency-Key": key or f"idem-{uuid7().hex}"}
        return await self.client.post(f"/v1/shop/items/{item_id}/purchase", headers=headers)

    # ------------------------------------------------------------------ сезонные события (5d)
    def admin(self, *perms: str) -> dict[str, str]:
        """Заголовки админа с правами и новым ``Idempotency-Key``."""
        token = self.issuer.issue(user_id=ADMIN, perms=["admin.access", *perms])
        return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"idem-{uuid7().hex}"}

    async def create_event(self, key: str, body: dict[str, Any]) -> httpx.Response:
        """``POST /admin/v1/events``."""
        return await self.client.post(
            "/admin/v1/events",
            headers=self.admin(EVENTS_MANAGE),
            json={"key": key, "body": body, "reason": "тест события"},
        )

    async def publish_event(
        self,
        key: str,
        body: dict[str, Any],
        *,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        create: bool = True,
    ) -> UUID:
        """Создать событие (при ``create``), запуск и опубликовать его; вернуть ``run_id``."""
        if create:
            created = await self.create_event(key, body)
            assert created.status_code == 201, created.text
        starts = starts_at or self.clock.now() - timedelta(days=1)
        ends = ends_at or self.clock.now() + timedelta(days=7)
        now = self.clock.now()
        backdated = starts < now - timedelta(seconds=30)  # API не создаёт запуск «задним числом»
        run = await self.client.post(
            f"/admin/v1/events/{key}/runs",
            headers=self.admin(EVENTS_MANAGE),
            json={
                "starts_at": (now if backdated else starts).isoformat(),
                "ends_at": ends.isoformat(),
                "reason": "тест",
            },
        )
        assert run.status_code == 201, run.text
        if backdated:
            assert self.infra.sessions is not None
            async with self.infra.sessions() as session, session.begin():
                await session.execute(
                    text("UPDATE event_runs SET starts_at = :s WHERE id = :i"),
                    {"s": starts, "i": UUID(run.json()["id"])},
                )
        event = await self.client.get(f"/admin/v1/events/{key}", headers=self.admin(EVENTS_MANAGE))
        published = await self.client.post(
            f"/admin/v1/events/{key}/runs/{run.json()['id']}/publish",
            headers=self.admin(EVENTS_PUBLISH),
            json={"body_sha256": event.json()["body_sha256"], "reason": "тест публикации"},
        )
        assert published.status_code == 200, published.text
        return UUID(run.json()["id"])

    async def claim(self, user: UUID, key: str, find: str) -> httpx.Response:
        """``POST /v1/events/{key}/finds/{find}/claim``."""
        headers = {**self.user(user), "Idempotency-Key": f"idem-{uuid7().hex}"}
        return await self.client.post(f"/v1/events/{key}/finds/{find}/claim", headers=headers)

    def season_standings(
        self,
        places: list[tuple[UUID, int]],
        *,
        season_id: UUID,
        chunk: int = 0,
        chunks_total: int = 1,
    ) -> EventEnvelope:
        """``rating.SeasonStandingsFinalized``."""
        base = load_example("rating.SeasonStandingsFinalized")
        payload = {
            **base.payload,
            "season_id": str(season_id),
            "chunk": chunk,
            "chunks_total": chunks_total,
            "entries": [{"user_id": str(u), "place": p, "rating": 1500} for u, p in places],
        }
        return base.model_copy(update={"aggregate_id": f"{season_id}:{chunk}", "payload": payload})


@pytest.fixture
def make_config() -> Callable[..., dict[str, Any]]:
    """Построитель тела конфига из примера контракта (правки — ``a__b=value``)."""
    return config_body


@pytest.fixture
def fake_clock_start() -> FakeClock:
    """Часы сервиса: пятница 2026-10-23 12:00 UTC."""
    return FakeClock(START)


@pytest.fixture
async def progression(
    database_url: str,
    infra: InfraProvider,
    token_issuer: TestTokenIssuer,
    fake_clock_start: FakeClock,
) -> AsyncIterator[ProgressionService]:
    """Сервис progression: реальный Postgres, события через EventProcessor."""
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    s3 = infra.s3_endpoint()
    settings = Settings(
        database_url=SecretStr(database_url),
        redis_url=SecretStr(infra.redis_url()),
        s3=S3Settings(
            endpoint=s3.url,
            access_key=SecretStr(s3.access_key),
            secret_key=SecretStr(s3.secret_key),
        ),
        progression=ProgressionSettings(cosmetics_bucket=f"gv-cosmetics-{uuid7().hex[-12:]}"),
    )
    runtime = await Infrastructure.open(settings, TABLES, clock=fake_clock_start)
    assert runtime.redis is not None
    await runtime.redis.flushdb()  # счётчики попыток прошлых тестов общего Redis
    container = container_for(runtime)
    await container.media_store.ensure_bucket()
    assert runtime.sessions is not None
    assert DEFINITION.build_event_handlers is not None
    dead_letters = FakeDeadLetterSink()

    async def no_sleep(_: float) -> None:
        return None

    processor = EventProcessor(
        group="progression.consumer",
        registry=DEFINITION.build_event_handlers(runtime),
        session_factory=runtime.sessions,
        inbox=Inbox(TABLES.inbox),
        dead_letters=dead_letters,
        clock=fake_clock_start,
        retry=RetryPolicy(max_attempts=2, base_delay=0.0, max_delay=0.0),
        sleep=no_sleep,
    )
    app = create_app(DEFINITION, runtime, jwt_verifier=token_issuer.verifier())
    try:
        async with service_client(app) as client:
            yield ProgressionService(
                client, runtime, container, processor, dead_letters, token_issuer, fake_clock_start
            )
    finally:
        await runtime.close()
