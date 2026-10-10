"""Фикстуры тестов game. Инфраструктура и общие фейки — из ``gv_testkit``.

Здесь — то, чего нет в testkit:

* минимальные тестовые плагины ядра под ключами сидов (``audio_intro``, ``daily`` …) —
  используются, только пока в установленном ``gv_game_kernel`` нет встроенных плагинов
  (``gv_game_kernel.builtin``, поток A); после слияния A тесты автоматически идут на
  реальных плагинах;
* совместимость со скелетом ядра шага 0a: если переходы ``round.py`` и методы
  ``ModeEngine`` ещё не реализованы (``NotImplementedError``), на время сессии тестов они
  подменяются минимальной реализацией правил D9 (после слияния A подмена не включается);
* фейк ``AssetPort`` (журнал выдач, сбои 410/503) и сборка сервиса на реальных Postgres
  и Redis (фикстура ``game``).
"""

from __future__ import annotations

import dataclasses
import functools
import math
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

import httpx
import pytest
import yaml
from game_service.infrastructure.container import GameAdapters
from game_service.infrastructure.db.tables import TABLES
from game_service.infrastructure.limits import RedisRateLimiter
from game_service.infrastructure.plugins import CatalogRegistry, build_registry, builtin_registrar
from game_service.main import DEFINITION, build_event_handlers, build_periodic_jobs, build_routers
from game_service.settings import GameLimits, GameSettings, Settings
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from gv_core.errors import DependencyUnavailableError, GoneError
from gv_core.ids import uuid7
from gv_core.random import SeededRandom
from gv_game_kernel import (
    AnswerInput,
    AssetPort,
    AssetRef,
    ClueAsset,
    ClueView,
    DrawContext,
    LevelSpec,
    MatchContext,
    MatchResult,
    MatchVerdict,
    MediaItemRef,
    ModeEngine,
    PlayableFor,
    PoolQuery,
    RoundFinishedError,
    RoundOutcome,
    RoundResult,
    RoundState,
    params_hash,
)
from gv_game_kernel import round as kernel_round
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


# ============================================================== тестовые плагины ядра
class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _LeveledParams(_Params):
    levels: tuple[dict[str, float | int], ...] = Field(min_length=1)
    start_s: float | None = None


class _Leveled:
    params_schema: ClassVar[type[BaseModel]] = _LeveledParams
    supported_answer_formats: ClassVar[frozenset[str]] = frozenset({"classic"})
    kind: ClassVar[str] = "image"

    def levels(self, params: BaseModel) -> list[LevelSpec]:
        assert isinstance(params, _LeveledParams)
        return [LevelSpec(i + 1, dict(level)) for i, level in enumerate(params.levels)]

    async def reveal(
        self, asset: AssetRef, level: int, params: BaseModel, *, assets: AssetPort, subject: str
    ) -> ClueView:
        link = await assets.link(asset, level, subject)
        return ClueView(level, self.kind, link, self.levels(params)[level - 1].hint)

    async def final_view(
        self, asset: AssetRef, params: BaseModel, *, assets: AssetPort, subject: str
    ) -> ClueView:
        return await self.reveal(
            asset, len(self.levels(params)), params, assets=assets, subject=subject
        )


class AudioIntroClue(_Leveled):
    key: ClassVar[str] = "audio_intro"
    domains: ClassVar[frozenset[str]] = frozenset({"music"})
    kind: ClassVar[str] = "audio"


class FrameClue(_Leveled):
    key: ClassVar[str] = "frame_progressive"
    domains: ClassVar[frozenset[str]] = frozenset({"film"})


class _Years(_Params):
    from_: int | None = Field(default=None, alias="from")
    to: int | None = None


class _Pool(_Params):
    genres_any: tuple[str, ...] = ()
    years: _Years | None = None
    min_popularity: float | None = None
    kinds: tuple[str, ...] = ()
    min_playable: int | None = None


def _query(ctx: DrawContext, pool: _Pool, extra: _Pool | None = None) -> PoolQuery:
    genres = set(pool.genres_any) | set(extra.genres_any if extra else ())
    years = (extra.years if extra and extra.years else None) or pool.years
    return PoolQuery(
        domain=ctx.domain,
        kinds=frozenset(pool.kinds),
        genres_any=frozenset(genres),
        year_from=years.from_ if years else None,
        year_to=years.to if years else None,
        min_popularity=pool.min_popularity,
        playable_for=PlayableFor(ctx.clue_type, ctx.params_hash),
        exclude_ids=ctx.exclude_ids,
    )


class _DailyParams(_Params):
    size: int = Field(ge=1)
    timezone: str
    schedule_ahead_days: int = Field(ge=0)
    exclude_recent_days: int = Field(ge=0)
    pool: _Pool


class DailySource:
    key: ClassVar[str] = "daily"
    params_schema: ClassVar[type[BaseModel]] = _DailyParams

    async def draw(self, ctx: DrawContext, params: BaseModel, n: int) -> list[MediaItemRef]:
        assert isinstance(params, _DailyParams)
        found = await ctx.candidates.candidates(_query(ctx, params.pool), 5000)
        return SeededRandom(ctx.seed).sample(found, n) if len(found) >= n else []


class _Preset(_Params):
    id: str
    title_i18n: str
    filters: _Pool


class _RandomParams(_Params):
    pool: _Pool
    presets: tuple[_Preset, ...] = ()


@dataclass(frozen=True)
class _PresetView:
    id: str
    title_key: str


class RandomSource:
    key: ClassVar[str] = "random"
    params_schema: ClassVar[type[BaseModel]] = _RandomParams

    async def draw(self, ctx: DrawContext, params: BaseModel, n: int) -> list[MediaItemRef]:
        assert isinstance(params, _RandomParams)
        preset = next((p for p in params.presets if p.id == ctx.preset_id), None)
        found = await ctx.candidates.candidates(
            _query(ctx, params.pool, preset.filters if preset else None), 5000
        )
        return SeededRandom(ctx.seed).sample(found, n) if len(found) >= n else []

    def presets(self, params: BaseModel) -> list[_PresetView]:
        assert isinstance(params, _RandomParams)
        return [_PresetView(p.id, p.title_i18n) for p in params.presets]


class _MatcherParams(_Params):
    typo: tuple[dict[str, int], ...] = ()
    accept_same_title: bool = False


class TitleAliasMatcher:
    key: ClassVar[str] = "title_alias"
    params_schema: ClassVar[type[BaseModel]] = _MatcherParams

    def match(
        self, answer: AnswerInput, item: MediaItemRef, params: BaseModel, ctx: MatchContext
    ) -> MatchResult:
        if answer.media_item_id == item.id:
            return MatchResult(MatchVerdict.EXACT)
        if answer.text is not None and ctx.normalizer.normalize(answer.text) in item.aliases:
            return MatchResult(MatchVerdict.EXACT)
        for rule in ctx.partial_rules:
            if rule.check(answer, item, ctx):
                return MatchResult(MatchVerdict.PARTIAL, rule.kind)
        return MatchResult(MatchVerdict.MISS)


class ArtistPartial:
    kind: ClassVar[str] = "artist"
    domains: ClassVar[frozenset[str]] = frozenset({"music"})

    def check(self, answer: AnswerInput, item: MediaItemRef, ctx: MatchContext) -> bool:
        artists = {p.id for p in item.persons if p.role == "artist"}
        if answer.selected is not None:
            return bool(artists & {p.id for p in answer.selected.persons if p.role == "artist"})
        names = {ctx.normalizer.normalize(p.name) for p in item.persons if p.role == "artist"}
        return answer.text is not None and ctx.normalizer.normalize(answer.text) in names


class _ByLevelParams(_Params):
    points: tuple[int, ...] = Field(min_length=1)
    partial_ratio: float = Field(ge=0, le=1)


class ByLevelScorer:
    key: ClassVar[str] = "by_level"
    params_schema: ClassVar[type[BaseModel]] = _ByLevelParams

    def score(self, outcome: RoundOutcome, params: BaseModel) -> int:
        assert isinstance(params, _ByLevelParams)
        if outcome.result is RoundResult.EXACT and outcome.level is not None:
            return params.points[outcome.level - 1]
        if outcome.partial_level is not None and outcome.result is not RoundResult.VOID:
            return math.floor(params.points[outcome.partial_level - 1] * params.partial_ratio)
        return 0

    def max_score(self, params: BaseModel, levels_count: int) -> int:
        assert isinstance(params, _ByLevelParams)
        return params.points[0]


def register_test_plugins(registry: CatalogRegistry) -> None:
    """Минимальные плагины под ключами сидов (пока нет ``gv_game_kernel.builtin``)."""
    registry.register_clue(AudioIntroClue())
    registry.register_clue(FrameClue())
    registry.register_source(DailySource())
    registry.register_source(RandomSource())
    registry.register_matcher(TitleAliasMatcher())
    registry.register_partial(ArtistPartial())
    registry.register_scorer(ByLevelScorer())


def make_registry() -> CatalogRegistry:
    """Реестр тестов: встроенные плагины ядра, если они есть, иначе тестовые."""
    if builtin_registrar() is not None:
        return build_registry()
    registry = CatalogRegistry()
    register_test_plugins(registry)
    return registry


@pytest.fixture
def plugin_registry() -> CatalogRegistry:
    """Реестр плагинов тестов (см. ``make_registry``)."""
    return make_registry()


# ============================================================== совместимость со скелетом ядра
def _parsed(schema: type[BaseModel], params: dict[str, Any]) -> BaseModel:
    return schema.model_validate(params)


def _levels(self: ModeEngine) -> list[LevelSpec]:
    return self.clue.levels(_parsed(self.clue.params_schema, self.config.clue.params))


async def _draw(self: ModeEngine, ctx: DrawContext, n: int) -> list[MediaItemRef]:
    return await self.source.draw(
        ctx, _parsed(self.source.params_schema, self.config.source.params), n
    )


def _check(self: ModeEngine, answer: AnswerInput, item: MediaItemRef) -> MatchResult:
    ctx = MatchContext(self.config.domain, TitleNormalizer(), self.partials)
    params = _parsed(self.matcher.params_schema, self.config.answer.matcher.params)
    return self.matcher.match(answer, item, params, ctx)


def _score(self: ModeEngine, outcome: RoundOutcome) -> int:
    return self.scorer.score(
        outcome, _parsed(self.scorer.params_schema, self.config.scoring.params)
    )


def _max_score(self: ModeEngine, rounds: int) -> int:
    params = _parsed(self.scorer.params_schema, self.config.scoring.params)
    return self.scorer.max_score(params, len(_levels(self))) * rounds


def _next(state: RoundState, levels_count: int) -> RoundState:
    if state.level < levels_count:
        return replace(state, level=state.level + 1, attempts_on_level=0)
    outcome = RoundResult.PARTIAL if state.partial_awarded_level else RoundResult.MISS
    return replace(state, outcome=outcome)


def _apply_guess(state: RoundState, guess: Any, *, levels_count: int, session: Any) -> RoundState:
    if state.finished:
        raise RoundFinishedError("раунд уже завершён")
    attempts = state.attempts_on_level + 1
    after = replace(state, attempts_on_level=attempts, guesses=(*state.guesses, guess))
    if guess.verdict is MatchVerdict.EXACT:
        return replace(after, outcome=RoundResult.EXACT)
    if guess.verdict is MatchVerdict.PARTIAL and after.partial_awarded_level is None:
        after = replace(after, partial_awarded_level=state.level)
    return _next(after, levels_count) if attempts >= session.attempts_per_level else after


def _apply_skip(state: RoundState, *, levels_count: int, session: Any) -> RoundState:
    if state.finished:
        raise RoundFinishedError("раунд уже завершён")
    skip = kernel_round.Guess(kernel_round.GuessKind.SKIP, state.level, None, "")
    return _next(replace(state, guesses=(*state.guesses, skip)), levels_count)


def _give_up(state: RoundState, *, session: Any) -> RoundState:
    if state.finished:
        raise RoundFinishedError("раунд уже завершён")
    return replace(state, outcome=RoundResult.GAVE_UP)


def _void(state: RoundState) -> RoundState:
    if state.finished:
        raise RoundFinishedError("раунд уже завершён")
    return replace(state, outcome=RoundResult.VOID)


def _kernel_is_skeleton() -> bool:
    try:
        kernel_round.void(RoundState())
    except NotImplementedError:
        return True
    return False


@pytest.fixture(scope="session", autouse=True)
def kernel_compat() -> Iterator[None]:
    """Подмена нереализованных частей скелета ядра (только до слияния потока A)."""
    if not _kernel_is_skeleton():
        yield
        return
    with pytest.MonkeyPatch.context() as mp:
        for name, fn in {
            "apply_guess": _apply_guess,
            "apply_skip": _apply_skip,
            "give_up": _give_up,
            "void": _void,
        }.items():
            mp.setattr(kernel_round, name, fn)
        for name, fn in {
            "levels": _levels,
            "draw": _draw,
            "check": _check,
            "score": _score,
            "max_score": _max_score,
        }.items():
            mp.setattr(ModeEngine, name, fn)
        yield


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
        registry=make_registry(),
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
