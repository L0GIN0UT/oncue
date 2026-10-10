"""Игровой цикл соло-сессии: старт, выдача ассета уровня, ходы, брошенные сессии.

Античит (D11): ответ проверяет только сервер, ассет выдаётся только для уровня не выше
открытого и только в текущем раунде своей сессии. Ход — одна транзакция: строка сессии
под ``FOR UPDATE``, проверка ``expected_version``, ``Idempotency-Key`` и outbox в той же
транзакции (D12, §9 п. 3). Вызов media-pipeline за ссылкой уровня идёт вне транзакции,
чтобы не держать блокировку строки во время сетевого запроса.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog

from game_service.application.catalog import ModeCatalog
from game_service.application.commands.daily import ScheduleDailySets
from game_service.application.ports import GameUnitOfWork, Json, RateLimiter, UnitOfWorkFactory
from game_service.application.rules import ModeRules, is_pool_too_small
from game_service.application.views import SessionPresenter, clue_json, subject_of
from game_service.domain.daily import DailySet, GeneratedBy, day_in_zone
from game_service.domain.errors import (
    AssetUnavailableError,
    DailyAlreadyPlayedError,
    DailyNotReadyError,
    GuestNotAllowedError,
    PoolEmptyError,
    PresetNotFoundError,
    SessionNotFoundError,
    desync,
)
from game_service.domain.projection import CatalogItem
from game_service.domain.session import GameSession, PlayerRef, Round, RoundPlan
from gv_core.clock import Clock
from gv_core.errors import AppError, GoneError, InvalidInputError
from gv_core.ids import uuid7
from gv_core.random import Random
from gv_db.idempotency import request_fingerprint, run_idempotent
from gv_game_kernel import (
    AnswerInput,
    AssetPort,
    DrawContext,
    Guess,
    GuessKind,
    RoundOutcome,
    RoundResult,
    RoundState,
)

log = structlog.get_logger(__name__)

SEED_BYTES = 16
ANSWER_UNKNOWN_ITEM = "game.answer_unknown_item"


@dataclass(frozen=True, slots=True)
class PlayLimits:
    """Лимиты частоты игровых действий (§9 п. 4)."""

    guess_per_min: int
    clue_per_min: int
    sessions_per_hour: int
    guest_sessions_per_ip_per_hour: int
    fast_exact_ms: int
    candidate_limit: int


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


class _DesyncSignal(Exception):  # noqa: N818 — внутренний сигнал выхода из транзакции
    """``expected_version`` устарела: снимок собирается после отката транзакции."""


class _DailyRace(Exception):  # noqa: N818 — внутренний сигнал выхода из транзакции
    """Параллельный старт того же дейлика вставил сессию раньше."""


async def items_for(uow: GameUnitOfWork, session: GameSession) -> dict[UUID, CatalogItem]:
    """Единицы каталога для ревила завершённых раундов (до ревила не загружаются)."""
    ids = [r.media_item_id for r in session.rounds if r.finished and not r.is_void]
    return await uow.projection.get_items(ids) if ids else {}


class PlayService:
    """Use-cases игры: ``StartSession``, ``IssueClue``, ``SubmitGuess``, ``SkipLevel``,
    ``GiveUpRound``, ``AbandonStaleSessions``.
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
    ) -> None:
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
        self, player: Player, mode_key: str, preset_id: str | None, idempotency_key: str
    ) -> tuple[Json, bool]:
        """Начать сессию.

        Returns:
            Снимок и признак «создана сейчас» (``False`` — незавершённый дейлик дня, 200).

        Raises:
            ModeNotFoundError, GuestNotAllowedError, DailyAlreadyPlayedError,
            DailyNotReadyError, PoolEmptyError, PresetNotFoundError, GameRateLimitedError.
        """
        version, rules = await self._catalog.latest(mode_key)
        if player.ref.is_guest and not rules.config.session.allow_guest:
            raise GuestNotAllowedError("режим доступен только с аккаунтом")
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
        schedule = rules.schedule
        if schedule is not None:
            if preset_id is not None:
                raise PresetNotFoundError("у дейлика нет пресетов")
            day = day_in_zone(self._clock.now(), schedule.timezone)
            daily = await self._daily.ensure(rules, day, GeneratedBy.LAZY)
            if not daily.is_playable:
                raise DailyNotReadyError("набор дня не готов")
            if daily.mode_version != version.version:
                rules = await self._catalog.rules(mode_key, daily.mode_version)
        elif preset_id is not None and preset_id not in {p.id for p in rules.presets()}:
            raise PresetNotFoundError("у режима нет такого пресета")

        fingerprint = request_fingerprint("session.start", mode_key, {"preset_id": preset_id})
        try:
            async with self._uow() as uow:

                async def perform() -> Json:
                    return await self._create(uow, player, rules, daily, preset_id)

                response, _ = await run_idempotent(
                    uow.idempotency, player.actor_id, idempotency_key, fingerprint, perform
                )
                await uow.commit()
        except _DailyRace:
            assert daily is not None  # noqa: S101 — гонка возможна только у дейлика
            async with self._uow() as uow:
                existing = await uow.sessions.find_daily(mode_key, daily.day, player.ref)
            if existing is None:  # pragma: no cover — unique-индекс сработал, строка есть
                raise
            return await self._resume(existing, rules), False
        return response["snapshot"], bool(response["created"])

    async def _resume(self, existing: GameSession, rules: ModeRules) -> Json:
        if not existing.is_active:
            raise DailyAlreadyPlayedError(
                "дейлик уже сыгран", details={"session_id": str(existing.id)}
            )
        async with self._uow() as uow:
            items = await items_for(uow, existing)
        return await self._presenter.snapshot(existing, rules, items)

    async def _create(
        self,
        uow: GameUnitOfWork,
        player: Player,
        rules: ModeRules,
        daily: DailySet | None,
        preset_id: str | None,
    ) -> Json:
        if daily is not None:
            existing = await uow.sessions.find_daily(rules.key, daily.day, player.ref)
            if existing is not None:
                items = await items_for(uow, existing)
                if not existing.is_active:
                    raise DailyAlreadyPlayedError(
                        "дейлик уже сыгран", details={"session_id": str(existing.id)}
                    )
                return {
                    "created": False,
                    "snapshot": await self._presenter.snapshot(existing, rules, items),
                }
        seed = bytes(self._random.randrange(256) for _ in range(SEED_BYTES))
        if daily is not None:
            item_ids = list(daily.items)
        else:
            item_ids = await self._draw(uow, rules, seed, preset_id)
        ready = await uow.projection.ready_assets(item_ids, rules.clue_type, rules.params_hash)
        plan = [RoundPlan(i, ready[i].to_ref() if i in ready else None) for i in item_ids]
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
            levels_count=rules.levels_count,
            max_points=rules.round_max,
            idle_timeout_s=rules.config.session.abandon_after_min * 60,
            now=now,
        )
        if not await uow.sessions.add(session):
            raise _DailyRace
        uow.track(session)
        log.info(
            "game.session_started",
            session_id=str(session.id),
            mode_key=rules.key,
            mode_version=rules.version,
            guest=player.ref.is_guest,
        )
        return {"created": True, "snapshot": await self._presenter.snapshot(session, rules, {})}

    async def _draw(
        self, uow: GameUnitOfWork, rules: ModeRules, seed: bytes, preset_id: str | None
    ) -> list[UUID]:
        ctx = DrawContext(
            mode_key=rules.key,
            domain=rules.domain,
            clue_type=rules.clue_type,
            params_hash=rules.params_hash,
            seed=seed,
            candidates=uow.candidates,
            preset_id=preset_id,
        )
        n = rules.rounds
        try:
            items = await rules.draw(ctx, n)
        except AppError as exc:
            if is_pool_too_small(exc):
                raise PoolEmptyError("в пуле режима мало играбельных единиц") from exc
            raise
        if len(items) < n:
            raise PoolEmptyError("в пуле режима мало играбельных единиц")
        return [item.id for item in items]

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
        if r.asset is None:  # pragma: no cover — раунд без ассета аннулирован при старте
            raise AssetUnavailableError("ассет раунда недоступен")
        try:
            view = await rules.reveal(
                r.asset, level, assets=self._assets, subject=subject_of(session_id)
            )
        except GoneError as exc:
            await self._void(player, session_id, index)
            raise AssetUnavailableError("ассет раунда недоступен — раунд не засчитан") from exc
        await self._mark_started(player, session_id, index)
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
            r = session.void_round(index, self._clock.now())
            if r is None:
                return
            await self._after_move(uow, session)
            await uow.commit()
        log.warning("game.round_void", session_id=str(session_id), round=index)

    async def _after_move(self, uow: GameUnitOfWork, session: GameSession) -> None:
        if not session.is_active and session.counts_in_daily_stats and session.day is not None:
            await uow.stats.record(
                session.mode_key,
                session.day,
                [(r.index, str(r.state.outcome)) for r in session.rounds],
            )
        await uow.sessions.save(session)
        uow.track(session)

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
    ) -> Json:
        """Ответ на текущем уровне раунда."""
        await self._limiter.hit(
            "guess", str(player.actor_id), limit=self._limits.guess_per_min, window_s=60
        )
        body = {
            "round_index": round_index,
            "expected_version": expected_version,
            "media_item_id": str(media_item_id) if media_item_id else None,
            "text": text,
        }

        async def compute(
            uow: GameUnitOfWork, session: GameSession, r: Round, rules: ModeRules
        ) -> tuple[RoundState, str | None]:
            answer, kind, display = await self._answer(uow, session, media_item_id, text)
            target = await uow.projection.get_item(r.media_item_id)
            if target is None:  # pragma: no cover — единица раунда пришла из проекции
                raise AssetUnavailableError("единица раунда недоступна")
            match = rules.check(answer, target.to_ref())
            new = rules.guess(
                r.state,
                Guess(kind=kind, level=r.state.level, verdict=match.verdict, display=display),
            )
            return new, str(match.verdict)

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
        media_item_id: UUID | None,
        text: str | None,
    ) -> tuple[AnswerInput, GuessKind, str]:
        if media_item_id is not None:
            selected = await uow.projection.get_item(media_item_id)
            if selected is None or selected.domain != session.domain:
                raise InvalidInputError(
                    "такой единицы нет в каталоге режима", code=ANSWER_UNKNOWN_ITEM
                )
            return (
                AnswerInput(media_item_id=media_item_id, selected=selected.to_ref()),
                GuessKind.ITEM,
                selected.title,
            )
        if text is None:  # pragma: no cover — схема запроса требует одно из двух
            raise InvalidInputError("нужен ответ")
        return AnswerInput(text=text), GuessKind.TEXT, text

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
        ) -> tuple[RoundState, str | None]:
            return rules.skip(r.state), None

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
        ) -> tuple[RoundState, str | None]:
            return rules.give_up(r.state), None

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
        compute: Callable[
            [GameUnitOfWork, GameSession, Round, ModeRules],
            Awaitable[tuple[RoundState, str | None]],
        ],
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
                    new_state, result = await compute(uow, session, r, rules)
                    now = self._clock.now()
                    points = self._points(rules, r, new_state, now)
                    session.apply(index, new_state, points=points, now=now)
                    await self._after_move(uow, session)
                    items = await items_for(uow, session)
                    snapshot = await self._presenter.snapshot(session, rules, items)
                    return {
                        "result": result,
                        "round": snapshot["rounds"][index],
                        "session": snapshot,
                    }

                response, _ = await run_idempotent(
                    uow.idempotency, player.actor_id, idempotency_key, fingerprint, perform
                )
                await uow.commit()
        except _DesyncSignal:
            raise desync(await self.snapshot(player, session_id)) from None
        return response

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
