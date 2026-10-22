"""Тестовые двойники match до слияния потока A этапа 4a.

Простые детерминированные реализации протоколов ядра, которых ещё нет в ``gv_game_kernel``:
``ContestRule`` (``race``, ``parallel``), Scorer ``party_speed`` и ``MatchOutcome.rank``;
порты match (план game, ссылки media-pipeline, хранилища) в памяти. После слияния A тесты
переходят на реальные плагины и ``gv_testkit.match`` (DSL, ``FakeRoundPlanPort``,
``FakeRealtime``, ``FakeLinkPort``).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from uuid import UUID

from match_service.application.ports import MatchRow, MyMatchRow
from match_service.application.rules import RulesFactory
from match_service.domain import errors
from match_service.domain.errors import MatchInvalidError, MatchUnavailableError
from match_service.domain.events import MatchStarted, PlayerRated, match_finished
from match_service.domain.model import (
    AnswerKey,
    LevelAsset,
    MatchState,
    PersonKey,
    PlannedRound,
    RoundPlan,
)
from match_service.domain.room import Room
from pydantic import BaseModel, ConfigDict

from gv_core.ids import uuid7
from gv_game_kernel import (
    AssetRef,
    ClueAsset,
    ContestRoundState,
    Guess,
    LevelLockedError,
    MatchVerdict,
    ModeConfig,
    ParamIssue,
    PlayerRound,
    PlayerRoundView,
    PlayerStatus,
    PluginRegistry,
    RoundOutcome,
    RoundResult,
    RoundState,
    register_builtin,
    register_collection,
)
from gv_game_kernel.outcome import DuelOutcome, Placement
from gv_text import TitleNormalizer


# ----------------------------------------------------------------------------- ядро
class RaceParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    attempts_per_level: int
    lock_on_first_correct: bool


class ParallelParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    attempts_per_level: int
    end_when_all_answered: bool


@dataclass(frozen=True)
class _Rules:
    attempts_per_level: int
    skip_allowed: bool = False
    give_up_allowed: bool = False


def _done(pr: PlayerRound, attempts: int) -> bool:
    return not pr.active or pr.state.finished or pr.state.attempts_on_level >= attempts


class _Contest:
    """Общая часть двойников ``ContestRule``."""

    key: ClassVar[str]
    params_schema: ClassVar[type[BaseModel]]
    answer_formats: ClassVar[frozenset[str]] = frozenset({"classic", "pick"})

    @staticmethod
    def _lock(params: Any) -> bool:
        return bool(getattr(params, "lock_on_first_correct", False))

    @staticmethod
    def _all(params: Any) -> bool:
        return bool(getattr(params, "end_when_all_answered", False))

    def round_rules(self, params: Any) -> _Rules:
        return _Rules(params.attempts_per_level)

    def start_round(
        self, player_ids: Sequence[str], *, now_ms: int, window_ms: int, params: Any
    ) -> ContestRoundState:
        return ContestRoundState(
            level=1,
            level_opened_at_ms=now_ms,
            level_ends_at_ms=now_ms + window_ms,
            players={p: PlayerRound() for p in player_ids},
        )

    def apply_guess(
        self,
        state: ContestRoundState,
        player_id: str,
        guess: Guess,
        *,
        levels_count: int,
        now_ms: int,
        params: Any,
    ) -> ContestRoundState:
        del levels_count
        ends = state.level_ends_at_ms or 0
        if state.finished or state.level_locked or now_ms >= ends:
            raise LevelLockedError("уровень закрыт")
        pr = state.players[player_id]
        if _done(pr, params.attempts_per_level):
            raise LevelLockedError("попытки кончились")
        rs = replace(
            pr.state,
            level=state.level,
            attempts_on_level=pr.state.attempts_on_level + 1,
            guesses=(*pr.state.guesses, guess),
        )
        answered = pr.answered_at_ms
        if guess.verdict is MatchVerdict.EXACT:
            rs = replace(rs, outcome=RoundResult.EXACT)
            answered = now_ms
        elif guess.verdict is MatchVerdict.PARTIAL and rs.partial_awarded_level is None:
            rs = replace(rs, partial_awarded_level=state.level)
            answered = answered or now_ms
        players = {**state.players, player_id: replace(pr, state=rs, answered_at_ms=answered)}
        locked = state.level_locked or (guess.verdict is MatchVerdict.EXACT and self._lock(params))
        return replace(state, players=players, level_locked=locked)

    def level_closed(self, state: ContestRoundState, *, now_ms: int, params: Any) -> bool:
        if state.level_locked or now_ms >= (state.level_ends_at_ms or 0):
            return True
        return self._all(params) and all(
            _done(p, params.attempts_per_level) for p in state.players.values()
        )

    def _finish(self, state: ContestRoundState) -> ContestRoundState:
        players = {}
        for pid, pr in state.players.items():
            if not pr.state.finished:
                result = (
                    RoundResult.PARTIAL
                    if pr.state.partial_awarded_level is not None
                    else RoundResult.MISS
                )
                players[pid] = replace(pr, state=replace(pr.state, outcome=result))
            else:
                players[pid] = pr
        return replace(state, players=players, finished=True)

    def next_level(
        self,
        state: ContestRoundState,
        *,
        levels_count: int,
        now_ms: int,
        window_ms: int,
        params: Any,
    ) -> ContestRoundState:
        if state.level >= levels_count or self.round_finished(state, params=params):
            return self._finish(state)
        players = {
            pid: pr
            if pr.state.finished or not pr.active
            else replace(pr, state=replace(pr.state, level=state.level + 1, attempts_on_level=0))
            for pid, pr in state.players.items()
        }
        return replace(
            state,
            level=state.level + 1,
            level_opened_at_ms=now_ms,
            level_ends_at_ms=now_ms + window_ms,
            level_locked=False,
            players=players,
        )

    def round_finished(self, state: ContestRoundState, *, params: Any) -> bool:
        if state.finished:
            return True
        active = [p for p in state.players.values() if p.active]
        if all(p.state.finished for p in active):
            return True
        return self._lock(params) and any(
            p.state.outcome is RoundResult.EXACT for p in state.players.values()
        )

    def leave(self, state: ContestRoundState, player_id: str) -> ContestRoundState:
        pr = state.players.get(player_id)
        if pr is None:
            return state
        rs = pr.state if pr.state.finished else replace(pr.state, outcome=RoundResult.VOID)
        return replace(
            state, players={**state.players, player_id: replace(pr, active=False, state=rs)}
        )

    def outcome(self, state: ContestRoundState, player_id: str, *, params: Any) -> RoundOutcome:
        del params
        rs = state.players[player_id].state
        result = rs.outcome or (
            RoundResult.PARTIAL if rs.partial_awarded_level is not None else RoundResult.MISS
        )
        answered = state.players[player_id].answered_at_ms
        opened = state.level_opened_at_ms or 0
        return RoundOutcome(
            result=result,
            level=rs.level if result is RoundResult.EXACT else None,
            partial_level=rs.partial_awarded_level,
            elapsed_ms=max(0, answered - opened) if answered is not None else None,
        )

    def views(self, state: ContestRoundState) -> list[PlayerRoundView]:
        out = []
        for pid, pr in state.players.items():
            rs = pr.state
            if rs.outcome is RoundResult.EXACT:
                out.append(PlayerRoundView(pid, PlayerStatus.GUESSED, rs.level))
            elif rs.finished:
                out.append(PlayerRoundView(pid, PlayerStatus.MISSED))
            elif rs.attempts_on_level > 0 and rs.level == state.level and state.level_locked:
                out.append(PlayerRoundView(pid, PlayerStatus.ANSWERED))
            else:
                out.append(PlayerRoundView(pid, PlayerStatus.THINKING))
        return out


class RaceContest(_Contest):
    key = "race"
    params_schema = RaceParams
    answer_formats = frozenset({"classic"})


class ParallelContest(_Contest):
    key = "parallel"
    params_schema = ParallelParams


CONTESTS: dict[str, _Contest] = {"race": RaceContest(), "parallel": ParallelContest()}


class PartySpeedParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_points: int
    partial_ratio: float


class PartySpeedScorer:
    """Двойник ``party_speed``: точный — ``max_points``, частичный — ``× partial_ratio``."""

    key: ClassVar[str] = "party_speed"
    params_schema: ClassVar[type[BaseModel]] = PartySpeedParams
    presentation: ClassVar[str] = "score"
    requires_early_end: ClassVar[bool] = False

    def score(self, outcome: RoundOutcome, params: Any) -> int:
        if outcome.result is RoundResult.EXACT:
            return int(params.max_points)
        if outcome.result is RoundResult.PARTIAL:
            return max(1, round(params.max_points * params.partial_ratio))
        return 0

    def max_score(self, params: Any, levels_count: int) -> int:
        del levels_count
        return int(params.max_points)

    def cross_check(
        self, params: Any, *, config: ModeConfig, levels_count: int
    ) -> list[ParamIssue]:
        del params, config, levels_count
        return []


def rank(scores: Mapping[str, int], left: Collection[str]) -> list[Placement]:
    """Двойник ``MatchOutcome.rank``: ничьи — одно место, вышедшие — в конце."""
    order = sorted(scores, key=lambda p: (p in left, -scores[p], p))
    out: list[Placement] = []
    for i, pid in enumerate(order):
        prev = out[-1] if out else None
        same = prev is not None and prev.score == scores[pid] and (pid in left) == prev.left
        place = prev.place if same and prev is not None else i + 1
        out.append(Placement(pid, scores[pid], place, None, left=pid in left))
    if len(out) == 2:
        a, b = out
        if a.place == b.place:
            res = (DuelOutcome.DRAW, DuelOutcome.DRAW)
        else:
            res = (DuelOutcome.WIN, DuelOutcome.LOSS)
        out = [replace(a, outcome=res[0]), replace(b, outcome=res[1])]
    return out


def registry() -> PluginRegistry:
    """Реестр: встроенные плагины ядра + источник сборника + двойник ``party_speed``."""
    reg = PluginRegistry()
    register_builtin(reg)
    register_collection(reg)
    if reg.scorer("party_speed") is None:
        reg.register_scorer(PartySpeedScorer())
    return reg


def factory() -> RulesFactory:
    """Фабрика правил на двойниках."""
    return RulesFactory(registry(), CONTESTS.get, ranker=rank)


# ----------------------------------------------------------------------------- конфиги
CHAT = {
    "emotes": ["emote.gg", "emote.wow"],
    "phrases": ["phrase.good_luck"],
}
TYPO = [{"min_len": 5, "max_distance": 1}, {"min_len": 10, "max_distance": 2}]


def duel_body(**session: Any) -> dict[str, Any]:
    """``music.duel.classic``: 2 уровня, 3 попытки, опережение закрывает уровень."""
    return {
        "key": "music.duel.classic",
        "title_i18n": "modes.music.duel.classic.title",
        "domain": "music",
        "source": {"type": "random", "params": {"pool": {"kinds": ["track"], "min_playable": 20}}},
        "clue": {
            "type": "audio_intro",
            "params": {"start_s": 0, "levels": [{"seconds": 1}, {"seconds": 4}]},
        },
        "answer": {
            "format": "classic",
            "matcher": {"type": "title_alias", "params": {"typo": TYPO, "accept_same_title": True}},
            "partial": ["artist"],
        },
        "scoring": {"type": "by_level", "params": {"points": [12, 6], "partial_ratio": 0.5}},
        "session": {
            "kind": "duel",
            "players": {"min": 2, "max": 2},
            "rounds": 3,
            "countdown_s": 3,
            "level_windows_s": [10, 10],
            "reveal_s": 3,
            "contest": {
                "type": "race",
                "params": {"attempts_per_level": 3, "lock_on_first_correct": True},
            },
            "disconnect_grace_s": 20,
            "chat": CHAT,
            **session,
        },
    }


FRAME_LEVELS = [
    {"blur_px": 32, "crop": 0.5, "max_width": 960},
    {"blur_px": 12, "crop": 1.0, "max_width": 960},
    {"blur_px": 0, "crop": 1.0, "max_width": 960},
]


def party_body(**session: Any) -> dict[str, Any]:
    """``film.party.frame``: 3 уровня, параллельные ответы, ``party_speed``."""
    return {
        "key": "film.party.frame",
        "title_i18n": "modes.film.party.frame.title",
        "domain": "film",
        "source": {"type": "random", "params": {"pool": {"kinds": ["film"], "min_playable": 2}}},
        "clue": {"type": "frame_progressive", "params": {"levels": FRAME_LEVELS}},
        "answer": {
            "format": "classic",
            "matcher": {"type": "title_alias", "params": {"typo": TYPO, "accept_same_title": True}},
            "partial": [],
        },
        "scoring": {"type": "party_speed", "params": {"max_points": 100, "partial_ratio": 0.5}},
        "session": {
            "kind": "party",
            "players": {"min": 3, "max": 6},
            "rounds": 2,
            "countdown_s": 3,
            "level_windows_s": [15, 15, 15],
            "reveal_s": 4,
            "contest": {
                "type": "parallel",
                "params": {"attempts_per_level": 2, "end_when_all_answered": True},
            },
            "disconnect_grace_s": 30,
            "chat": CHAT,
            **session,
        },
    }


def lobby_body(**session: Any) -> dict[str, Any]:
    """``film.lobby.collection``: кастомное лобби по сборникам со свободным чатом."""
    body = party_body()
    body["key"] = "film.lobby.collection"
    body["title_i18n"] = "modes.film.lobby.collection.title"
    body["source"] = {
        "type": "collection",
        "params": {"round_options": [5, 10, "all"], "min_items": 5, "max_collections": 3},
    }
    body["session"] = {
        **body["session"],
        "kind": "lobby",
        "players": {"min": 2, "max": 6},
        "rounds": 10,
        "lobby": {
            "overridable": {
                "rounds": [5, 10, 12],
                "level_window_s": [15, 25],
                "players_max": [4, 6],
                "visibility": ["public", "private"],
            },
            "visibility": "private",
        },
        "chat": {**CHAT, "free_text": True},
        **session,
    }
    return body


# ----------------------------------------------------------------------------- порты
_NORM = TitleNormalizer()
TITLES = ["Брат", "Сталкер", "Солярис", "Зеркало", "Ирония судьбы", "Кин-дза-дза"] * 9


def answer_key(index: int, domain: str) -> AnswerKey:
    title = TITLES[index]
    return AnswerKey(
        media_item_id=uuid7(),
        domain=domain,
        kind="film" if domain == "film" else "track",
        title=title,
        year=1990 + index,
        aliases=(_NORM.normalize(title),),
        title_aliases=(_NORM.normalize(title),),
        persons=(PersonKey(uuid7(), "Режиссёр", _NORM.normalize("Режиссёр"), "director"),),
        partial=(),
    )


@dataclass
class FakePlans:
    """``RoundPlanPort``: план из тела конфига; ``error`` — отказ game."""

    bodies: dict[str, dict[str, Any]]
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    levels: int | None = None

    async def plan(
        self,
        *,
        mode_key: str,
        version: int,
        seed: str,
        rounds: int,
        collection_ids: Sequence[UUID],
        exclude_ids: Sequence[UUID],
    ) -> RoundPlan:
        self.calls.append(
            {
                "mode_key": mode_key,
                "version": version,
                "seed": seed,
                "rounds": rounds,
                "collection_ids": list(collection_ids),
                "exclude_ids": list(exclude_ids),
            }
        )
        if self.error is not None:
            raise self.error
        body = self.bodies[mode_key]
        n_levels = self.levels or len(body["clue"]["params"]["levels"])
        plan = tuple(
            PlannedRound(
                index=i,
                answer=answer_key(i, body["domain"]),
                levels=tuple(
                    LevelAsset(level=lv, asset_set_id=uuid7()) for lv in range(1, n_levels + 1)
                ),
            )
            for i in range(rounds)
        )
        return RoundPlan(
            mode_version=version,
            body=body,
            seed=seed,
            rounds=plan,
            collections=tuple((c, 1) for c in collection_ids),
        )


def pool_too_small() -> MatchInvalidError:
    return MatchInvalidError("пул мал", code=errors.POOL_TOO_SMALL, details={"available": 1})


def game_down() -> MatchUnavailableError:
    return MatchUnavailableError("game недоступен", code=errors.GAME_UNAVAILABLE)


@dataclass
class FakeAssets:
    """``AssetPort``: ссылка на каждый запрос; ``gone`` — недоступные наборы."""

    gone: set[UUID] = field(default_factory=set)
    calls: list[tuple[UUID, int, str]] = field(default_factory=list)

    async def link(self, asset: AssetRef, level: int, subject: str) -> ClueAsset:
        from gv_core.errors import GoneError

        self.calls.append((asset.asset_set_id, level, subject))
        if asset.asset_set_id in self.gone:
            raise GoneError("ассет недоступен")
        return ClueAsset(
            level=level,
            url=f"/media/a/{uuid7().hex}.jpg",
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
            mime="image/jpeg",
        )


@dataclass
class MemoryStore:
    """``RoomStore`` в памяти (снимок — сам объект)."""

    rooms: dict[str, Room] = field(default_factory=dict)

    async def load(self, code: str) -> Room | None:
        return self.rooms.get(code)

    async def create(self, room: Room) -> bool:
        if room.code in self.rooms:
            return False
        self.rooms[room.code] = room
        return True

    async def open_rooms(self, cursor: Any, limit: int) -> Any:
        return [], None

    async def active_rooms(self, cursor: Any, limit: int) -> Any:
        return [], None


@dataclass
class MemoryPlayerRooms:
    """``PlayerRooms`` в памяти."""

    rooms: dict[UUID, str] = field(default_factory=dict)

    async def get(self, user_id: UUID) -> str | None:
        return self.rooms.get(user_id)

    async def claim(self, user_id: UUID, code: str) -> bool:
        current = self.rooms.setdefault(user_id, code)
        return current == code

    async def assign(self, user_id: UUID, code: str) -> None:
        self.rooms[user_id] = code

    async def release(self, user_id: UUID, code: str) -> None:
        if self.rooms.get(user_id) == code:
            del self.rooms[user_id]


@dataclass
class MemoryRecords:
    """``MatchRecords`` в памяти: события и повторы."""

    started: dict[UUID, MatchStarted] = field(default_factory=dict)
    finished: dict[UUID, Any] = field(default_factory=dict)
    fail: bool = False

    async def record_started(self, event: MatchStarted, meta: dict[str, Any]) -> bool:
        del meta
        if self.fail:
            raise ConnectionError("postgres недоступен (двойник)")
        if event.match_id in self.started:
            return False
        self.started[event.match_id] = event
        return True

    async def record_finished(self, room: Room, match: MatchState) -> bool:
        if self.fail:
            raise ConnectionError("postgres недоступен (двойник)")
        if match.match_id in self.finished:
            return False
        started = match.started_at_ms or match.created_at_ms
        self.finished[match.match_id] = match_finished(
            room,
            match,
            started_at=datetime.fromtimestamp(started / 1000, UTC),
            finished_at=datetime.fromtimestamp((match.finished_at_ms or started) / 1000, UTC),
        )
        return True

    async def get(self, match_id: UUID) -> MatchRow | None:
        return None

    async def mine(self, user_id: UUID, cursor: Any, limit: int) -> list[MyMatchRow]:
        return []

    async def participants(self, match_id: UUID) -> list[UUID] | None:
        return None

    async def rate(self, event: PlayerRated, *, actor_id: UUID, key: str, fingerprint: str) -> bool:
        return True


START = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def later(seconds: float) -> timedelta:
    return timedelta(seconds=seconds)


__all__ = ["RoundState"]
