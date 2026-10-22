"""Двойники портов самого match и онлайн-шаблоны из сидов.

Ядро — настоящее (встроенные плагины, ``ModeEngine.contest_*``); фейки внешних портов —
``gv_testkit.match`` (``FakeRoundPlanPort``, ``FakeRealtime``, ``FakeLinkPort``). Здесь —
только то, чего нет в testkit: шаблоны сидов, ответ плана game по запросу match, хранилища
комнат и итогов в памяти для unit-тестов владельца.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from match_service.application.ports import MatchRow, MyMatchRow, RoundPlanRequest
from match_service.application.rules import RulesFactory
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

from gv_core.ids import uuid7
from gv_testkit.game import builtin_registry
from gv_text import TitleNormalizer

START = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


# ----------------------------------------------------------------------------- шаблоны
def factory() -> RulesFactory:
    """Фабрика правил на встроенных плагинах ядра (как в процессе match)."""
    return RulesFactory(builtin_registry())


_SEEDS = next(
    p / "seeds" / "game" / "modes"
    for p in Path(__file__).resolve().parents
    if (p / "seeds" / "game" / "modes").is_dir()
)
ONLINE_KEYS = (
    "music.duel.classic",
    "film.duel.frame",
    "film.party.frame",
    "music.lobby.classic",
    "film.lobby.collection",
)


def seed_body(key: str, **session: Any) -> dict[str, Any]:
    """Онлайн-шаблон из ``seeds/game/modes`` (с переопределениями ``session``)."""
    body: dict[str, Any] = yaml.safe_load((_SEEDS / f"{key}.yaml").read_text(encoding="utf-8"))
    body["session"] = {**body["session"], **session}
    return body


def seed_bodies() -> dict[str, dict[str, Any]]:
    """Все онлайн-шаблоны сидов."""
    return {key: seed_body(key) for key in ONLINE_KEYS}


# ----------------------------------------------------------------------------- план
_NORM = TitleNormalizer()
TITLES = ["Брат", "Сталкер", "Солярис", "Зеркало", "Ирония судьбы", "Кин-дза-дза"] * 9
DIRECTOR = "Алексей Балабанов"


def answer_key(index: int, domain: str) -> AnswerKey:
    """Ключ ответа раунда ``index`` (название из ``TITLES``)."""
    title = TITLES[index]
    return AnswerKey(
        media_item_id=uuid7(),
        domain=domain,
        kind="film" if domain == "film" else "track",
        title=title,
        year=1990 + index,
        aliases=(_NORM.normalize(title),),
        title_aliases=(_NORM.normalize(title),),
        persons=(
            PersonKey(
                uuid7(),
                DIRECTOR,
                _NORM.normalize(DIRECTOR),
                "director" if domain == "film" else "artist",
            ),
        ),
        partial=(),
    )


def plan_factory(
    bodies: dict[str, dict[str, Any]], *, levels: int | None = None
) -> Callable[[RoundPlanRequest], RoundPlan]:
    """Ответ ``FakeRoundPlanPort`` по запросу match: план из тела шаблона."""

    def build(request: RoundPlanRequest) -> RoundPlan:
        body = bodies[request.mode_key]
        n_levels = levels or len(body["clue"]["params"]["levels"])
        rounds = tuple(
            PlannedRound(
                index=i,
                answer=answer_key(i, body["domain"]),
                levels=tuple(
                    LevelAsset(level=lv, asset_set_id=uuid7()) for lv in range(1, n_levels + 1)
                ),
            )
            for i in range(request.rounds)
        )
        return RoundPlan(
            mode_version=request.version,
            body=body,
            seed=request.seed,
            rounds=rounds,
            collections=tuple((c, 1) for c in request.collection_ids),
        )

    return build


# ----------------------------------------------------------------------------- хранилища
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
        return self.rooms.setdefault(user_id, code) == code

    async def assign(self, user_id: UUID, code: str) -> None:
        self.rooms[user_id] = code

    async def release(self, user_id: UUID, code: str) -> None:
        if self.rooms.get(user_id) == code:
            del self.rooms[user_id]


@dataclass
class MemoryRecords:
    """``MatchRecords`` в памяти: события начала и итога, повторы — no-op."""

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
