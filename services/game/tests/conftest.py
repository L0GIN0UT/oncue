"""Фикстуры тестов game. Инфраструктура и общие фейки — из ``gv_testkit``.

Здесь — то, чего нет в testkit: реестр с реальными плагинами ядра, фейк ``AssetPort``
(журнал выдач, сбои 410/503) и сборка сервиса на реальных Postgres и Redis
(фикстура ``game``).
"""

from __future__ import annotations

import dataclasses
import functools
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
import yaml
from game_service.infrastructure.container import GameAdapters
from game_service.infrastructure.db.tables import TABLES
from game_service.infrastructure.limits import RedisRateLimiter
from game_service.infrastructure.plugins import CatalogRegistry, build_registry
from game_service.main import DEFINITION, build_event_handlers, build_periodic_jobs, build_routers
from game_service.settings import GameLimits, GameSettings, Settings
from pydantic import SecretStr

from gv_core.errors import DependencyUnavailableError, GoneError
from gv_core.ids import uuid7
from gv_game_kernel import (
    AssetRef,
    ClueAsset,
    params_hash,
)
from gv_messaging import EventEnvelope, EventProcessor, Inbox, Outcome
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
from gv_text import TitleNormalizer

SEEDS_DIR = Path(__file__).resolve().parents[3] / "seeds" / "game" / "modes"
DAILY_SECRET = "test-daily-seed-secret-0123456789"
# 12:00 по Москве: день смены набора — 2026-10-10.
START = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
TODAY = START.date()


def seed_body(key: str) -> dict[str, Any]:
    """Тело сида режима."""
    body: dict[str, Any] = yaml.safe_load((SEEDS_DIR / f"{key}.yaml").read_text("utf-8"))
    return body


# ============================================================== реестр плагинов
@pytest.fixture
def plugin_registry() -> CatalogRegistry:
    """Реестр процесса: реальные встроенные плагины ядра."""
    return build_registry()


# ============================================================== фейк AssetPort
@dataclass
class FakeAssets:
    """``AssetPort`` в памяти: журнал выдач и сбои по набору ассетов."""

    clock: FakeClock
    issued: list[tuple[UUID, int, str]] = field(default_factory=list)
    gone: set[UUID] = field(default_factory=set)
    unavailable: bool = False

    async def link(self, asset: AssetRef, level: int, subject: str) -> ClueAsset:
        if asset.asset_set_id in self.gone:
            raise GoneError("набор выключен", code="asset.blocked")
        if self.unavailable:
            raise DependencyUnavailableError("media недоступен")
        self.issued.append((asset.asset_set_id, level, subject))
        token = uuid7().hex
        return ClueAsset(
            level=level,
            url=f"/media/a/{token}.webm",
            expires_at=self.clock.now() + timedelta(seconds=60),
            mime="audio/mp4",
        )


# ============================================================== сервис целиком
@dataclass
class GameService:
    """Сервис на реальных Postgres и Redis; media-pipeline — ``FakeAssets``."""

    client: httpx.AsyncClient
    infra: Infrastructure
    processor: EventProcessor
    issuer: TestTokenIssuer
    assets: FakeAssets
    clock: FakeClock
    definition: Any

    async def deliver(self, envelope: EventEnvelope) -> Outcome:
        return await self.processor.process(envelope.to_bytes(), source="test")

    async def run_job(self, name: str) -> JobResult:
        return await run_periodic_job(self.definition, name, self.infra)

    async def outbox(self) -> list[EventEnvelope]:
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            return await outbox_envelopes(session, TABLES.outbox)

    def admin(self, *perms: str) -> dict[str, str]:
        token = self.issuer.issue(perms=("admin.access", *perms))
        return {"Authorization": f"Bearer {token}"}

    async def add_item(
        self,
        *,
        domain: str = "music",
        title: str | None = None,
        year: int = 2005,
        genres: Sequence[str] = ("rock",),
        artist: tuple[UUID, str] | None = None,
        ready_for: str | None = None,
        version: int = 1,
    ) -> UUID:
        """Единица каталога (событие catalog) и, если задан режим, готовые ассеты под него."""
        item_id = uuid7()
        title = title or f"Тестовая единица {item_id.hex[-6:]}"
        base = load_example("catalog.MediaItemCreated")
        kind = "track" if domain == "music" else "film"
        persons = (
            [{"person_id": str(artist[0]), "name": artist[1], "role": "artist", "order": 0}]
            if artist
            else []
        )
        payload = {
            **base.payload,
            "media_item_id": str(item_id),
            "version": version,
            "domain": domain,
            "kind": kind,
            "title": title,
            "year": year,
            "aliases": [
                {
                    "text": title,
                    "normalized": TitleNormalizer().normalize(title),
                    "lang": "ru",
                    "kind": "title",
                }
            ],
            "persons": persons,
            "genres": list(genres),
        }
        envelope = base.model_copy(update={"aggregate_id": str(item_id), "payload": payload})
        assert await self.deliver(envelope) is Outcome.PROCESSED
        if ready_for is not None:
            await self.ready_assets(item_id, ready_for)
        return item_id

    async def ready_assets(
        self, item_id: UUID, mode_key: str, *, version: int = 1, body: dict[str, Any] | None = None
    ) -> UUID:
        """``AssetsReady`` под ``clue.params`` режима (D7)."""
        clue = (body or seed_body(mode_key))["clue"]
        base = load_example("media_pipeline.AssetsReady")
        asset_set_id = uuid7()
        payload = {
            **base.payload,
            "asset_set_id": str(asset_set_id),
            "media_item_id": str(item_id),
            "clue_type": clue["type"],
            "params_hash": params_hash(clue["params"]),
            "levels_count": len(clue["params"]["levels"]),
            "version": version,
        }
        envelope = base.model_copy(update={"aggregate_id": str(item_id), "payload": payload})
        assert await self.deliver(envelope) is Outcome.PROCESSED
        return asset_set_id


@pytest.fixture
def fake_clock_start() -> FakeClock:
    """Часы сервиса: 2026-10-10 12:00 МСК."""
    return FakeClock(START)


@pytest.fixture
async def game(
    database_url: str,
    infra: InfraProvider,
    token_issuer: TestTokenIssuer,
    fake_clock_start: FakeClock,
) -> AsyncIterator[GameService]:
    """Сервис game: реальные Postgres и Redis, сиды режимов, секрет наборов дня."""
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    settings = Settings(
        database_url=SecretStr(database_url),
        redis_url=SecretStr(infra.redis_url()),
        game=GameSettings(
            daily_seed_secret=SecretStr(DAILY_SECRET),
            bootstrap_modes_dir=SEEDS_DIR,
            share_url="https://oncue.test/",
        ),
        # Часы тестов стоят на одном моменте, а Redis общий для прогонов: окна лимитов
        # совпадали бы между запусками. Сами лимиты проверяет integration-тест лимитера.
        game_limits=GameLimits(
            guess_per_min=10_000,
            clue_per_min=10_000,
            sessions_per_hour=10_000,
            guest_sessions_per_ip_per_hour=10_000,
        ),
    )
    clock = fake_clock_start
    runtime = await Infrastructure.open(settings, TABLES, clock=clock)
    assert runtime.redis is not None
    assets = FakeAssets(clock)
    adapters = GameAdapters(
        registry=build_registry(),
        assets=assets,
        limiter=RedisRateLimiter(runtime.redis, clock),
        random=FakeRandom(7),
    )
    definition = dataclasses.replace(
        DEFINITION,
        build_routers=functools.partial(build_routers, adapters=adapters),
        build_event_handlers=functools.partial(build_event_handlers, adapters=adapters),
        build_periodic_jobs=functools.partial(build_periodic_jobs, adapters=adapters),
    )
    assert runtime.sessions is not None
    assert definition.build_event_handlers is not None
    processor = EventProcessor(
        group="game.consumer",
        registry=definition.build_event_handlers(runtime),
        session_factory=runtime.sessions,
        inbox=Inbox(TABLES.inbox),
        dead_letters=FakeDeadLetterSink(),
        clock=clock,
    )
    app = create_app(definition, runtime, jwt_verifier=token_issuer.verifier())
    try:
        async with service_client(app) as client:
            yield GameService(client, runtime, processor, token_issuer, assets, clock, definition)
    finally:
        await runtime.close()
