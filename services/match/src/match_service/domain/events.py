"""События match (топик ``match.match.v1``, ``contracts/events/match/*``, §2.2 плана).

Без ников и e-mail (ПДн): только идентификаторы. ``aggregate_id`` — ``match_id``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from gv_core.domain import DomainEvent
from match_service.domain.model import MatchState, RoundRecord, Standing
from match_service.domain.room import Room


@dataclass(frozen=True, kw_only=True)
class _MatchEvent(DomainEvent):
    aggregate_type: ClassVar[str] = "match"


@dataclass(frozen=True, kw_only=True)
class MatchStarted(_MatchEvent):
    """Матч начался (отсчёт закончился, план получен)."""

    match_id: UUID
    version: int
    room_code: str
    mode_key: str
    mode_version: int
    domain: str
    format: str
    ranked: bool
    ladder: str | None
    players: list[dict[str, Any]]  # Any: {user_id, slot} по схеме события
    started_at: datetime


@dataclass(frozen=True, kw_only=True)
class MatchFinished(_MatchEvent):
    """Матч завершён (в том числе досрочно); пишется одной транзакцией с итогом."""

    match_id: UUID
    version: int
    room_code: str
    mode_key: str
    mode_version: int
    domain: str
    format: str
    ranked: bool
    ladder: str | None
    players: list[dict[str, Any]]  # Any: строки итога по схеме события
    rounds: list[dict[str, Any]]  # Any: раунды по схеме события
    collection_ids: list[UUID]
    aborted: bool
    abort_reason: str | None
    started_at: datetime
    finished_at: datetime


@dataclass(frozen=True, kw_only=True)
class PlayerRated(_MatchEvent):
    """Похвала или жалоба после матча (D14; потребитель — этап 5)."""

    match_id: UUID
    from_id: UUID
    to_id: UUID
    kind: str
    reason: str | None
    at: datetime


ANONYMOUS = UUID(int=0)
"""Игрок с удалённым аккаунтом в итоге: его идентификатор не публикуется (ПДн)."""


def _hide(user_id: UUID, hidden: frozenset[UUID]) -> UUID:
    return ANONYMOUS if user_id in hidden else user_id


def _standing(s: Standing, hidden: frozenset[UUID]) -> dict[str, Any]:  # Any: примитивы JSON
    return {
        "user_id": _hide(s.user_id, hidden),
        "slot": s.slot,
        "score": s.score,
        "place": s.place,
        "outcome": s.outcome,
        "finish": s.finish.value,
    }


def _round(r: RoundRecord, hidden: frozenset[UUID]) -> dict[str, Any]:  # Any: JSON
    return {
        "index": r.index,
        "media_item_id": r.media_item_id,
        "results": [
            {
                "user_id": _hide(x.user_id, hidden),
                "level": x.level,
                "partial": x.partial,
                "score": x.score,
                "elapsed_ms": x.elapsed_ms,
            }
            for x in r.results
        ],
    }


def match_started(room: Room, match: MatchState, started_at: datetime) -> MatchStarted:
    """Событие начала матча."""
    mode = room.mode
    return MatchStarted(
        aggregate_id=str(match.match_id),
        match_id=match.match_id,
        version=room.version,
        room_code=room.code,
        mode_key=mode.key,
        mode_version=match.mode_version,
        domain=mode.domain,
        format=mode.kind,
        ranked=mode.ranked,
        ladder=mode.ladder,
        players=[
            {"user_id": p.user_id, "slot": p.slot}
            for p in sorted(match.players.values(), key=lambda p: p.slot)
        ],
        started_at=started_at,
    )


def match_finished(
    room: Room,
    match: MatchState,
    *,
    started_at: datetime,
    finished_at: datetime,
    hidden: frozenset[UUID] = frozenset(),
) -> MatchFinished:
    """Событие итога матча; ``hidden`` — удалённые аккаунты (без их идентификаторов)."""
    mode = room.mode
    return MatchFinished(
        aggregate_id=str(match.match_id),
        match_id=match.match_id,
        version=room.version,
        room_code=room.code,
        mode_key=mode.key,
        mode_version=match.mode_version,
        domain=mode.domain,
        format=mode.kind,
        ranked=mode.ranked,
        ladder=mode.ladder,
        players=[_standing(s, hidden) for s in match.standings],
        rounds=[_round(r, hidden) for r in match.records if not r.void],
        collection_ids=[cid for cid, _ in match.collections],
        aborted=match.abort_reason is not None,
        abort_reason=match.abort_reason,
        started_at=started_at,
        finished_at=finished_at,
    )
