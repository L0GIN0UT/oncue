"""Игровой цикл соло-сессии: старт, выдача ассета уровня, ходы, ревил, брошенные сессии.

Античит (D11): ответ проверяет только сервер, ассет выдаётся только для уровня не выше
открытого и только в текущем раунде своей сессии. Ход — одна транзакция: строка сессии
под ``FOR UPDATE``, проверка ``expected_version``, ``Idempotency-Key`` и outbox в той же
транзакции (D12, §9 п. 3). Вызов media-pipeline за ссылкой уровня идёт вне транзакции,
чтобы не держать блокировку строки во время сетевого запроса.

Этап 3a (ADR 0023, D4): после каждого завершённого раунда сервис спрашивает у ядра
``should_end`` (досрочный конец — план отбрасывается) и ``followups`` (сетка дописывает
тур), затем выдаёт следующий раунд плана (``_settle``). В той же транзакции: рекорд и
гистограмма при завершении (``features.records``, только пользователи) и статистика
выбора сетки (только пользователи).

Этап 3b (ADR 0028): сессия по сборнику — ``collection_id`` и ``rounds`` при старте.
Сборник берётся из проекции (опубликован и не скрыт), число раундов выбирает ядро
(``rounds_for``), в сессии фиксируются ``collection_id`` и ``content_version``. Ассет
раунда — сначала набор сборника, затем каталожный (ADR 0029).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog

from game_service.application.artwork import ARTWORK_CLUE_TYPE, ArtworkProfiles
from game_service.application.catalog import ModeCatalog
from game_service.application.commands.daily import ScheduleDailySets
from game_service.application.ports import GameUnitOfWork, Json, RateLimiter, UnitOfWorkFactory
from game_service.application.rules import ModeRules, is_pool_too_small
from game_service.application.views import (
    SessionPresenter,
    clue_json,
    outcomes_until,
    round_items,
    subject_of,
)
from game_service.domain.daily import DailySet, GeneratedBy
from game_service.domain.errors import (
    AssetUnavailableError,
    CollectionDomainMismatchError,
    CollectionNotFoundError,
    CollectionNotSupportedError,
    DailyAlreadyPlayedError,
    DailyNotReadyError,
    GuestNotAllowedError,
    PoolEmptyError,
    PresetNotFoundError,
    SessionNotFoundError,
    SubjectsNotSupportedError,
    desync,
)
from game_service.domain.projection import CatalogItem, CollectionProj
from game_service.domain.session import (
    GameSession,
    PlannedRound,
    PlayerRef,
    Round,
    RoundAssets,
    RoundOption,
    RoundSubject,
)
from gv_core.clock import Clock
from gv_core.errors import AppError, GoneError, InvalidInputError
from gv_core.ids import uuid7
from gv_core.random import Random
from gv_db.idempotency import request_fingerprint, run_idempotent
from gv_game_kernel import (
    AnswerInput,
    AssetPort,
    CollectionRequiredError,
    MediaItemRef,
    ModeEngine,
    OptionSpec,
    PersonRef,
    RoundDraw,
    RoundNotFinishedError,
    RoundOutcome,
    RoundPick,
    RoundResult,
    RoundsNotAllowedError,
    RoundState,
)
from gv_game_kernel import RoundOption as RoundsChoice

log = structlog.get_logger(__name__)

SEED_BYTES = 16
ANSWER_UNKNOWN_ITEM = "game.answer_unknown_item"


@dataclass(frozen=True, slots=True)
class PlayLimits:
    """Лимиты частоты игровых действий (§9 п. 4, план этапа 3a §11 п. 4)."""

    guess_per_min: int
    clue_per_min: int
    sessions_per_hour: int
    guest_sessions_per_ip_per_hour: int
    fast_exact_ms: int
    candidate_limit: int
    final_clue_per_min: int = 30


@dataclass(frozen=True, slots=True)
class Player:
    """Субъект запроса: игрок и IP (для лимита гостевых сессий)."""

    ref: PlayerRef
    ip: str | None = None

    @property
    def actor_id(self) -> UUID:
        """Ключ идемпотентности и лимитов (у гостя — guest_id)."""
        if self.ref.id is None:  # pragma: no cover — Principal всегда несёт sub
            raise SessionNotFoundError("сессия не найдена")
        return self.ref.id


@dataclass(frozen=True, slots=True)
class MoveOutcome:
    """Что посчитал ход: новое состояние раунда, вердикт и выбранный вариант."""

    state: RoundState
    result: str | None
    choice: str | None = None


class _DesyncSignal(Exception):  # noqa: N818 — внутренний сигнал выхода из транзакции
    """``expected_version`` устарела: снимок собирается после отката транзакции."""


class _DailyRace(Exception):  # noqa: N818 — внутренний сигнал выхода из транзакции
    """Параллельный старт того же дейлика вставил сессию раньше."""


async def items_for(uow: GameUnitOfWork, session: GameSession) -> dict[UUID, CatalogItem]:
    """Единицы каталога для снимка и итога (цель идущего раунда не загружается)."""
    ids = round_items(session)
    return await uow.projection.get_items(ids) if ids else {}


def planned_of(draw: RoundDraw) -> PlannedRound:
    """Состав раунда ядра → раунд плана (только идентификаторы)."""
    return PlannedRound(
        target=draw.target.id,
        shown=tuple(item.id for item in draw.shown),
        options=tuple(
            RoundOption(
                id=o.id,
                label_key=o.label_key,
                media_item_id=o.item.id if o.item is not None else None,
            )
            for o in draw.options
        ),
        subjects=tuple(RoundSubject(p.id, p.name, p.role) for p in draw.subjects),
    )


def _ref(items: Mapping[UUID, CatalogItem], item_id: UUID, domain: str) -> MediaItemRef:
    item = items.get(item_id)
    if item is not None:
        return item.to_ref()
    # Единица пропала из проекции (не должно случаться: события только дополняют её) —
    # ссылка без метаданных сохраняет состав раунда для ядра.
    return MediaItemRef(id=item_id, domain=domain, kind="", title="")


def draw_of(round_: Round, items: Mapping[UUID, CatalogItem], domain: str) -> RoundDraw:
    """Состав выданного раунда для ядра (единицы — из проекции)."""
    return RoundDraw(
        target=_ref(items, round_.media_item_id, domain),
        shown=tuple(_ref(items, s.media_item_id, domain) for s in round_.shown),
        options=tuple(
            OptionSpec(
                id=o.id,
                label_key=o.label_key,
                item=_ref(items, o.media_item_id, domain) if o.media_item_id else None,
            )
            for o in round_.options
        ),
        subjects=tuple(PersonRef(id=s.id, name=s.name, role=s.role) for s in round_.subjects),
    )


class PlayService:
    """Use-cases игры: ``StartSession``, ``IssueClue``, ``SubmitGuess`` (в том числе
    ``{option_id}``), ``SkipLevel``, ``GiveUpRound``, ``FinalClue``, ``AbandonStaleSessions``.
    """

    def __init__(
        self,
        *,
        uow: UnitOfWorkFactory,
        clock: Clock,
        random: Random,
        catalog: ModeCatalog,
        daily: ScheduleDailySets,
        assets: AssetPort,
        limiter: RateLimiter,
        presenter: SessionPresenter,
        limits: PlayLimits,
        artwork: ArtworkProfiles | None = None,
    ) -> None:
        self._artwork = artwork or ArtworkProfiles({})
        self._uow = uow
        self._clock = clock
        self._random = random
        self._catalog = catalog
        self._daily = daily
        self._assets = assets
        self._limiter = limiter
        self._presenter = presenter
        self._limits = limits

    # ------------------------------------------------------------------ чтение
    async def _load(
        self, uow: GameUnitOfWork, player: Player, session_id: UUID, *, lock: bool = False
    ) -> GameSession:
        session = await uow.sessions.get(session_id, for_update=lock)
        if session is None:
            raise SessionNotFoundError("сессия не найдена")
        session.require_owner(player.ref)
        return session

    async def snapshot(self, player: Player, session_id: UUID) -> Json:
        """Снимок своей сессии (чужая — 404)."""
        async with self._uow() as uow:
            session = await self._load(uow, player, session_id)
            rules = await self._catalog.rules(session.mode_key, session.mode_version)
            items = await items_for(uow, session)
        return await self._presenter.snapshot(session, rules, items)

    # ------------------------------------------------------------------ старт
    async def start(
        self,
        player: Player,
        mode_key: str,
        preset_id: str | None,
        idempotency_key: str,
        subject_ids: Sequence[UUID] = (),
        *,
        collection_id: UUID | None = None,
        rounds: RoundsChoice | None = None,
    ) -> tuple[Json, bool]:
        """Начать сессию.

        Args:
            collection_id: Сборник для режима с источником из сборника (ADR 0028).
            rounds: Число раундов из ``round_options`` режима сборника; ``None`` — первый
                вариант.

        Returns:
            Снимок и признак «создана сейчас» (``False`` — незавершённая сессия периода, 200).

        Raises:
            ModeNotFoundError, ModeNotSoloError (онлайн-режим), GuestNotAllowedError,
            DailyAlreadyPlayedError,
            DailyNotReadyError, PoolEmptyError, PresetNotFoundError, SubjectsNotSupportedError,
            SubjectRequiredError, TooManySubjectsError, GameRateLimitedError,
            CollectionNotFoundError, CollectionRequiredError, CollectionNotSupportedError,
            CollectionDomainMismatchError, RoundsNotAllowedError, PoolTooSmallError (сборник).
        """
        version, rules = await self._catalog.latest(mode_key)
        # Онлайн-режим играет match (ADR 0032): отказ — до лимитов, набора периода и
        # транзакции, чтобы попытка не тратила ни лимит, ни строку сессии.
        solo = rules.engine.solo_session
        if player.ref.is_guest and not solo.allow_guest:
            raise GuestNotAllowedError("режим доступен только с аккаунтом")
        subjects = sorted(set(subject_ids))
        if subjects and rules.subject_query() is None:
            raise SubjectsNotSupportedError("режим не выбирает персон")
        collection = await self._collection(rules, collection_id, rounds)
        await self._limiter.hit(
            "sessions", str(player.actor_id), limit=self._limits.sessions_per_hour, window_s=3600
        )
        if player.ref.is_guest and player.ip:
            await self._limiter.hit(
                "guest_sessions_ip",
                player.ip,
                limit=self._limits.guest_sessions_per_ip_per_hour,
                window_s=3600,
            )
        daily: DailySet | None = None
        if rules.schedule is not None:
            if preset_id is not None:
                raise PresetNotFoundError("у планового режима нет пресетов")
            period = rules.period_start(self._clock.now())
            daily = await self._daily.ensure(rules, period, GeneratedBy.LAZY)
            if not daily.is_playable:
                raise DailyNotReadyError("набор периода не готов")
            if daily.mode_version != version.version:
                rules = await self._catalog.rules(mode_key, daily.mode_version)
        elif preset_id is not None and preset_id not in {p.id for p in rules.presets()}:
            raise PresetNotFoundError("у режима нет такого пресета")

        fingerprint = request_fingerprint(
            "session.start",
            mode_key,
            {
                "preset_id": preset_id,
                "subject_ids": [str(s) for s in subjects],
                "collection_id": str(collection_id) if collection_id else None,
                "rounds": rounds,
            },
        )
        try:
            async with self._uow() as uow:

                async def perform() -> Json:
                    return await self._create(
                        uow,
                        player,
                        rules,
                        daily=daily,
                        preset_id=preset_id,
                        subjects=subjects,
                        collection=collection,
                        rounds=rounds,
                    )

                response, _ = await run_idempotent(
                    uow.idempotency, player.actor_id, idempotency_key, fingerprint, perform
                )
                await uow.commit()
        except _DailyRace:
            assert daily is not None  # noqa: S101 — гонка возможна только у планового набора
            async with self._uow() as uow:
                existing = await uow.sessions.find_daily(mode_key, daily.day, player.ref)
            if existing is None:  # pragma: no cover — unique-индекс сработал, строка есть
                raise
            return await self._resume(existing, rules), False
        return response["snapshot"], bool(response["created"])

    async def _collection(
        self, rules: ModeRules, collection_id: UUID | None, rounds: RoundsChoice | None
    ) -> CollectionProj | None:
        """Сборник сессии из проекции (до лимитов: ошибка входа не тратит попытку).

        Raises:
            CollectionNotSupportedError: сборник передан режиму без источника из сборника.
            CollectionRequiredError: режиму из сборника не передан сборник.
            RoundsNotAllowedError: ``rounds`` у режима без выбора числа раундов.
            CollectionNotFoundError: сборника нет, он скрыт или удалён.
            CollectionDomainMismatchError: домен сборника не равен домену режима.
        """
        uses = rules.engine.uses_collection
        if collection_id is not None and not uses:
            raise CollectionNotSupportedError("режим не играется по сборнику")
        if uses and collection_id is None:
            raise CollectionRequiredError("режиму нужен сборник")
        if rounds is not None and not uses:
            raise RoundsNotAllowedError(
                "у режима нет выбора числа раундов", details={"requested": rounds, "options": []}
            )
        if collection_id is None:
            return None
        async with self._uow() as uow:
            found = await uow.collections.get(collection_id)
        if found is None or not found.is_playable:
            raise CollectionNotFoundError("сборник не найден")
        if found.domain != rules.domain:
            raise CollectionDomainMismatchError("сборник другого домена")
        return found

    async def _resume(self, existing: GameSession, rules: ModeRules) -> Json:
        if not existing.is_active:
            raise DailyAlreadyPlayedError(
                "попытка периода уже сыграна", details={"session_id": str(existing.id)}
            )
        async with self._uow() as uow:
            items = await items_for(uow, existing)
        return await self._presenter.snapshot(existing, rules, items)

    async def _create(
        self,
        uow: GameUnitOfWork,
        player: Player,
        rules: ModeRules,
        *,
        daily: DailySet | None,
        preset_id: str | None,
        subjects: Sequence[UUID],
        collection: CollectionProj | None = None,
        rounds: RoundsChoice | None = None,
    ) -> Json:
        if daily is not None:
            existing = await uow.sessions.find_daily(rules.key, daily.day, player.ref)
            if existing is not None:
                items = await items_for(uow, existing)
                if not existing.is_active:
                    raise DailyAlreadyPlayedError(
                        "попытка периода уже сыграна", details={"session_id": str(existing.id)}
                    )
                return {
                    "created": False,
                    "snapshot": await self._presenter.snapshot(existing, rules, items),
                }
        seed = bytes(self._random.randrange(256) for _ in range(SEED_BYTES))
        plan = await self._plan(
            uow,
            rules,
            seed=seed,
            daily=daily,
            preset_id=preset_id,
            subjects=subjects,
            collection=collection,
            rounds=rounds,
        )
        now = self._clock.now()
        session = GameSession.start(
            uuid7(),
            player=player.ref,
            mode_key=rules.key,
            mode_version=rules.version,
            domain=rules.domain,
            source_type=rules.source_type,
            clue_type=rules.clue_type,
            day=daily.day if daily else None,
            preset_id=preset_id,
            seed=seed,
            plan=plan,
            round_max=rules.round_max,
            idle_timeout_s=rules.engine.solo_session.abandon_after_min * 60,
            now=now,
            progressive=rules.is_progressive,
            collection_id=collection.collection_id if collection else None,
            collection_version=collection.content_version if collection else None,
            collection_tags=collection.tags if collection else (),
        )
        await self._settle(uow, session, rules, now)
        if not await uow.sessions.add(session):
            raise _DailyRace
        uow.track(session)
        log.info(
            "game.session_started",
            session_id=str(session.id),
            mode_key=rules.key,
            mode_version=rules.version,
            guest=player.ref.is_guest,
            collection_id=str(collection.collection_id) if collection else None,
        )
        items = await items_for(uow, session)
        return {"created": True, "snapshot": await self._presenter.snapshot(session, rules, items)}

    async def _plan(
        self,
        uow: GameUnitOfWork,
        rules: ModeRules,
        *,
        seed: bytes,
        daily: DailySet | None,
        preset_id: str | None,
        subjects: Sequence[UUID],
        collection: CollectionProj | None = None,
        rounds: RoundsChoice | None = None,
    ) -> list[PlannedRound]:
        """Разыграть начальные раунды: из набора периода, сборника или источником режима.

        У сборника нехватка единиц — 422 ``game.pool_too_small`` ядра (игрок выбрал
        вариант), у остальных режимов — 503 ``game.pool_empty``.
        """
        if daily is not None:
            items = await uow.projection.get_items(daily.items)
            draws = rules.engine.compose_rounds([_ref(items, i, rules.domain) for i in daily.items])
        elif collection is not None:
            ctx = rules.engine.draw_context(
                seed=seed,
                candidates=uow.candidates_for(rules.reveal_playable_for),
                collections=uow.collection_port(rules.reveal_playable_for),
                collection_id=collection.collection_id,
                candidate_limit=self._limits.candidate_limit,
            )
            count = await rules.engine.rounds_for(ctx, rounds)
            draws = await rules.engine.draw_rounds(ctx, count)
        else:
            ctx = rules.engine.draw_context(
                seed=seed,
                candidates=uow.candidates_for(rules.reveal_playable_for),
                preset_id=preset_id,
                subject_ids=frozenset(subjects),
                candidate_limit=self._limits.candidate_limit,
            )
            try:
                draws = await rules.engine.draw_rounds(ctx)
            except AppError as exc:
                if is_pool_too_small(exc):
                    raise PoolEmptyError("в пуле режима мало играбельных единиц") from exc
                raise
        if not draws:
            raise PoolEmptyError("в пуле режима мало играбельных единиц")
        return [planned_of(d) for d in draws]

    # ------------------------------------------------------------------ ход сессии
    async def _round_assets(
        self,
        uow: GameUnitOfWork,
        rules: ModeRules,
        planned: PlannedRound,
        scope: UUID | None = None,
        *,
        domain: str,
    ) -> RoundAssets:
        """Наборы раунда; ``scope`` — сборник сессии: его свой набор первым (ADR 0029)."""
        ids = [planned.target, *planned.shown]
        ready = await uow.projection.ready_assets(
            ids, rules.clue_type, rules.params_hash, scope=scope
        )
        reveal = None
        reveal_for = rules.reveal_playable_for
        if reveal_for is not None:
            found = await uow.projection.ready_assets(
                [planned.target], reveal_for.clue_type, reveal_for.params_hash, scope=scope
            )
            reveal = found[planned.target].to_ref() if planned.target in found else None
        artwork = None
        artwork_hash = self._artwork.params_hash(domain)
        if artwork_hash is not None:
            # Картинка ответа — всегда каталожная (своих у сборников нет, ADR 0073).
            found = await uow.projection.ready_assets(
                [planned.target], ARTWORK_CLUE_TYPE, artwork_hash
            )
            artwork = found[planned.target].to_ref() if planned.target in found else None
        target = ready.get(planned.target)
        return RoundAssets(
            asset=target.to_ref() if target is not None else None,
            reveal=reveal,
            artwork=artwork,
            shown={i: ready[i].to_ref() for i in planned.shown if i in ready},
        )

    async def _picks(self, uow: GameUnitOfWork, session: GameSession) -> list[RoundPick]:
        ids = [i for r in session.rounds for i in r.plan().item_ids()]
        items = await uow.projection.get_items(ids)
        return [
            RoundPick(
                draw=draw_of(r, items, session.domain),
                outcome=ModeEngine.outcome(r.state, elapsed_ms=r.elapsed_ms),
                option_id=r.choice,
            )
            for r in session.rounds
        ]

    async def _settle(
        self, uow: GameUnitOfWork, session: GameSession, rules: ModeRules, now: datetime
    ) -> bool:
        """Довести сессию до хода игрока: следующий раунд, досрочный конец или итог.

        Returns:
            ``True`` — сессия завершилась в этом вызове.
        """
        was_active = session.is_active
        while session.needs_next:
            if session.rounds:
                history = outcomes_until(session, len(session.rounds) - 1, inclusive=True)
                if rules.engine.should_end(history):
                    session.end_by_rule(now)
                    break
                if rules.is_progressive:
                    picks = await self._picks(uow, session)
                    session.extend_plan([planned_of(d) for d in rules.engine.followups(picks)])
            planned = session.next_planned
            if planned is None:
                session.complete(now)
                break
            session.materialize_next(
                await self._round_assets(
                    uow, rules, planned, session.collection_id, domain=session.domain
                ),
                levels_count=rules.levels_count,
                max_points=rules.round_max,
                stage=rules.engine.round_stage(len(session.rounds)),
                now=now,
            )
        return was_active and not session.is_active

    async def _after_move(
        self, uow: GameUnitOfWork, session: GameSession, rules: ModeRules, *, finished_now: bool
    ) -> None:
        if finished_now:
            if session.counts_in_daily_stats and session.day is not None:
                await uow.stats.record(
                    session.mode_key,
                    session.day,
                    [(r.index, str(r.state.outcome)) for r in session.rounds],
                )
            user_id = session.player.user_id
            if rules.records_enabled and user_id is not None and session.finished_at is not None:
                # D11: рекорд и гистограмма — в транзакции завершения, гостей нет.
                await uow.records.save_result(
                    user_id,
                    session.mode_key,
                    session.score,
                    session_id=session.id,
                    at=session.finished_at,
                )
        await uow.sessions.save(session)
        uow.track(session)

    # ------------------------------------------------------------------ ассет уровня
    async def issue_clue(self, player: Player, session_id: UUID, index: int, level: int) -> Json:
        """Ссылка на ассет уровня ``level`` текущего раунда (уровень ≤ открытого).

        Raises:
            SessionNotFoundError, LevelLockedError, RoundFinishedError, RoundNotActiveError,
            AssetUnavailableError (раунд становится ``void``), AssetsUnavailableError,
            GameRateLimitedError.
        """
        await self._limiter.hit(
            "clue", str(player.actor_id), limit=self._limits.clue_per_min, window_s=60
        )
        async with self._uow() as uow:
            session = await self._load(uow, player, session_id)
            r = session.clue_round(index, level)
        rules = await self._catalog.rules(session.mode_key, session.mode_version)
        if r.asset is None:  # pragma: no cover — раунд без ассета аннулирован при выдаче
            raise AssetUnavailableError("ассет раунда недоступен")
        try:
            view = await rules.clue_view(
                r.state, r.asset, level, assets=self._assets, subject=subject_of(session_id)
            )
        except GoneError as exc:
            await self._void(player, session_id, index)
            raise AssetUnavailableError("ассет раунда недоступен — раунд не засчитан") from exc
        await self._mark_started(player, session_id, index)
        return clue_json(view)

    async def final_clue(self, player: Player, session_id: UUID, index: int) -> Json:
        """Свежая ссылка ревила завершённого не-``void`` раунда своей сессии (D13).

        Ссылки ревила живут 60 с (ADR 0012): фронт скачивает ассет сразу, а при ошибке или
        истечении просит новую ссылку здесь.

        Raises:
            SessionNotFoundError: сессии нет или она чужая.
            RoundNotFinishedError: раунд идёт или ещё не выдан (409).
            AssetUnavailableError: раунд ``void`` или ассет выключен (410).
            GameRateLimitedError: лимит ``GV_GAME_LIMITS__FINAL_CLUE_PER_MIN``.
        """
        await self._limiter.hit(
            "final_clue", str(player.actor_id), limit=self._limits.final_clue_per_min, window_s=60
        )
        async with self._uow() as uow:
            session = await self._load(uow, player, session_id)
        if not 0 <= index < len(session.rounds) or not session.rounds[index].finished:
            raise RoundNotFinishedError("ревил доступен только после раунда")
        r = session.rounds[index]
        if r.is_void or r.asset is None:
            raise AssetUnavailableError("раунд не засчитан — ревила нет")
        rules = await self._catalog.rules(session.mode_key, session.mode_version)
        try:
            view = await self._presenter.issue_final_clue(session, r, rules)
        except GoneError as exc:
            raise AssetUnavailableError("ассет ревила недоступен") from exc
        return clue_json(view)

    async def _mark_started(self, player: Player, session_id: UUID, index: int) -> None:
        async with self._uow() as uow:
            session = await self._load(uow, player, session_id, lock=True)
            if session.mark_clue_issued(index, self._clock.now()):
                await uow.sessions.save(session)
                await uow.commit()

    async def _void(self, player: Player, session_id: UUID, index: int) -> None:
        async with self._uow() as uow:
            session = await self._load(uow, player, session_id, lock=True)
            now = self._clock.now()
            r = session.void_round(index, now)
            if r is None:
                return
            rules = await self._catalog.rules(session.mode_key, session.mode_version)
            finished_now = await self._settle(uow, session, rules, now)
            await self._after_move(uow, session, rules, finished_now=finished_now)
            await uow.commit()
        log.warning("game.round_void", session_id=str(session_id), round=index)

    # ------------------------------------------------------------------ ходы
    async def guess(
        self,
        player: Player,
        session_id: UUID,
        *,
        round_index: int,
        expected_version: int,
        media_item_id: UUID | None,
        text: str | None,
        idempotency_key: str,
        option_id: str | None = None,
    ) -> Json:
        """Ответ на текущем уровне раунда: единица, текст или вариант (``pick``)."""
        await self._limiter.hit(
            "guess", str(player.actor_id), limit=self._limits.guess_per_min, window_s=60
        )
        body = {
            "round_index": round_index,
            "expected_version": expected_version,
            "media_item_id": str(media_item_id) if media_item_id else None,
            "text": text,
            "option_id": option_id,
        }

        async def compute(
            uow: GameUnitOfWork, session: GameSession, r: Round, rules: ModeRules
        ) -> MoveOutcome:
            items = await uow.projection.get_items(r.plan().item_ids())
            draw = draw_of(r, items, session.domain)
            answer, display = await self._answer(
                uow,
                session,
                r,
                items,
                media_item_id=media_item_id,
                text=text,
                option_id=option_id,
            )
            step = rules.engine.answer(r.state, answer, draw.target, display=display, draw=draw)
            return MoveOutcome(step.state, str(step.match.verdict), option_id)

        return await self._move(
            player,
            session_id,
            action="session.guess",
            body=body,
            idempotency_key=idempotency_key,
            expected_version=expected_version,
            index=round_index,
            compute=compute,
        )

    async def _answer(
        self,
        uow: GameUnitOfWork,
        session: GameSession,
        r: Round,
        items: Mapping[UUID, CatalogItem],
        *,
        media_item_id: UUID | None,
        text: str | None,
        option_id: str | None,
    ) -> tuple[AnswerInput, str]:
        if option_id is not None:
            option = r.option(option_id)
            item = items.get(option.media_item_id) if option and option.media_item_id else None
            display = (option.label_key if option else None) or (item.title if item else option_id)
            return AnswerInput(option_id=option_id), display
        if media_item_id is not None:
            selected = await uow.projection.get_item(media_item_id)
            if selected is None or selected.domain != session.domain:
                raise InvalidInputError(
                    "такой единицы нет в каталоге режима", code=ANSWER_UNKNOWN_ITEM
                )
            return AnswerInput(
                media_item_id=media_item_id, selected=selected.to_ref()
            ), selected.title
        if text is None:  # pragma: no cover — схема запроса требует одно из трёх
            raise InvalidInputError("нужен ответ")
        return AnswerInput(text=text), text

    async def skip(
        self,
        player: Player,
        session_id: UUID,
        *,
        round_index: int,
        expected_version: int,
        idempotency_key: str,
    ) -> Json:
        """Пропустить уровень."""
        await self._limiter.hit(
            "guess", str(player.actor_id), limit=self._limits.guess_per_min, window_s=60
        )

        async def compute(
            _uow: GameUnitOfWork, _session: GameSession, r: Round, rules: ModeRules
        ) -> MoveOutcome:
            return MoveOutcome(rules.skip(r.state), None)

        body = {"round_index": round_index, "expected_version": expected_version}
        return await self._move(
            player,
            session_id,
            action="session.skip",
            body=body,
            idempotency_key=idempotency_key,
            expected_version=expected_version,
            index=round_index,
            compute=compute,
        )

    async def give_up(
        self,
        player: Player,
        session_id: UUID,
        *,
        round_index: int,
        expected_version: int,
        idempotency_key: str,
    ) -> Json:
        """Сдаться в раунде."""
        await self._limiter.hit(
            "guess", str(player.actor_id), limit=self._limits.guess_per_min, window_s=60
        )

        async def compute(
            _uow: GameUnitOfWork, _session: GameSession, r: Round, rules: ModeRules
        ) -> MoveOutcome:
            return MoveOutcome(rules.give_up(r.state), None)

        body = {"round_index": round_index, "expected_version": expected_version}
        return await self._move(
            player,
            session_id,
            action="session.give_up",
            body=body,
            idempotency_key=idempotency_key,
            expected_version=expected_version,
            index=round_index,
            compute=compute,
        )

    async def _move(
        self,
        player: Player,
        session_id: UUID,
        *,
        action: str,
        body: Mapping[str, Any],
        idempotency_key: str,
        expected_version: int,
        index: int,
        compute: Callable[[GameUnitOfWork, GameSession, Round, ModeRules], Awaitable[MoveOutcome]],
    ) -> Json:
        fingerprint = request_fingerprint(action, session_id, body)
        try:
            async with self._uow() as uow:

                async def perform() -> Json:
                    session = await self._load(uow, player, session_id, lock=True)
                    if session.version != expected_version:
                        raise _DesyncSignal  # noqa: TRY301 — выход из транзакции с откатом
                    rules = await self._catalog.rules(session.mode_key, session.mode_version)
                    r = session.active_round(index)
                    move = await compute(uow, session, r, rules)
                    now = self._clock.now()
                    points = self._points(rules, r, move.state, now)
                    session.apply(index, move.state, points=points, now=now, choice=move.choice)
                    stats = await self._pick_stats(uow, session, r, rules)
                    finished_now = await self._settle(uow, session, rules, now)
                    await self._after_move(uow, session, rules, finished_now=finished_now)
                    items = await items_for(uow, session)
                    snapshot = await self._presenter.snapshot(session, rules, items)
                    return {
                        "result": move.result,
                        "round": snapshot["rounds"][index],
                        "session": snapshot,
                        "stats": stats,
                    }

                response, _ = await run_idempotent(
                    uow.idempotency, player.actor_id, idempotency_key, fingerprint, perform
                )
                await uow.commit()
        except _DesyncSignal:
            raise desync(await self.snapshot(player, session_id)) from None
        return response

    async def _pick_stats(
        self, uow: GameUnitOfWork, session: GameSession, r: Round, rules: ModeRules
    ) -> Json | None:
        """Статистика выбора сетки «согласны N %» (D10): учитываются только пользователи,
        гость видит долю без своего голоса."""
        if not rules.is_progressive or session.day is None or not r.finished or r.choice is None:
            return None
        pair = r.winner_loser()
        if pair is None:
            return None
        winner, loser = pair
        if session.player.user_id is not None:
            await uow.bracket_stats.add_pick(session.mode_key, session.day, winner, loser)
        pct = await uow.bracket_stats.agree_pct(session.mode_key, session.day, winner, loser)
        return {"agree_pct": pct} if pct is not None else None

    def _points(self, rules: ModeRules, r: Round, state: RoundState, now: datetime) -> int:
        if not state.finished or state.outcome is RoundResult.VOID:
            return 0
        elapsed = max(0, int((now - r.started_at).total_seconds() * 1000)) if r.started_at else None
        exact = state.outcome is RoundResult.EXACT
        if (
            exact
            and state.level == 1
            and elapsed is not None
            and elapsed < self._limits.fast_exact_ms
        ):
            # Сигнал для детектора аномалий (этап 5): точный ответ слишком быстро.
            log.warning("game.fast_exact", round=r.index, elapsed_ms=elapsed)
        outcome = RoundOutcome(
            result=state.outcome or RoundResult.MISS,
            level=state.level if exact else None,
            partial_level=state.partial_awarded_level,
            elapsed_ms=elapsed,
        )
        return rules.score(outcome)

    # ------------------------------------------------------------------ брошенные
    async def abandon_stale(self, batch: int) -> int:
        """Бросить активные сессии без ходов дольше ``abandon_after_min`` режима."""
        now = self._clock.now()
        async with self._uow() as uow:
            ids = await uow.sessions.stale_ids(now, batch)
        done = 0
        for session_id in ids:
            async with self._uow() as uow:
                session = await uow.sessions.get(session_id, for_update=True)
                if session is None or not session.is_active:
                    continue
                idle = (now - session.last_move_at).total_seconds()
                if idle < session.idle_timeout_s or not session.abandon(now):
                    continue
                await uow.sessions.save(session)
                await uow.commit()
                done += 1
        if done:
            log.info("game.sessions_abandoned", count=done)
        return done
