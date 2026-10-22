"""История матчей и оценки игроков (R9, D14): Postgres, без владельцев комнат."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from gv_core.clock import Clock
from gv_db.idempotency import request_fingerprint
from match_service.application.ports import Json, MatchRecords, MatchRow, RateLimiter
from match_service.application.queries.rooms import decode_cursor, encode_cursor
from match_service.domain import errors
from match_service.domain.errors import MatchForbiddenError, MatchInvalidError, MatchNotFoundError
from match_service.domain.events import PlayerRated

ANONYMOUS = UUID(int=0)
"""Игрок с удалённым аккаунтом в истории (``identity.UserDeleted``): без идентификатора."""


def _mode(row: MatchRow) -> Json:
    return {
        "key": row.mode_key,
        "version": row.mode_version,
        "domain": row.domain,
        "kind": row.format,
        "title_key": row.title_key,
        "clue_type": row.clue_type,
        "answer_format": row.answer_format,
        "ranked": row.ranked,
    }


def match_result(row: MatchRow, *, participant: bool) -> Json:
    """``MatchResult``: публичный итог (ники, места, очки); код комнаты — только участникам."""
    return {
        "match_id": str(row.match_id),
        "room_code": row.room_code if participant else None,
        "mode": _mode(row),
        "started_at": row.started_at.isoformat(),
        "finished_at": row.finished_at.isoformat(),
        "finish": row.finish,
        "players": [
            {
                "user_id": str(p["user_id"] or ANONYMOUS),
                "nickname": p.get("nickname") or "",
                "slot": p["slot"],
                "place": p["place"],
                "score": p["score"],
                "outcome": p["outcome"],
                "finish": p["finish"],
            }
            for p in row.players
        ],
        "rounds": [
            {
                "index": r["round_index"],
                "media_item_id": str(r["media_item_id"]),
                "void": r["void"],
                "results": list(r["results"]),
            }
            for r in row.rounds
        ],
        "collection_ids": [str(c) for c in row.collection_ids],
    }


class MatchQueries:
    """Итог матча и «Мои матчи»."""

    def __init__(self, records: MatchRecords) -> None:
        self._records = records

    async def get(self, match_id: UUID, viewer: UUID | None = None) -> Json:
        """Завершённый матч (идущий — 404); ``viewer=None`` — админка (всё)."""
        row = await self._records.get(match_id)
        if row is None:
            raise MatchNotFoundError("матча нет или он идёт", code=errors.MATCH_NOT_FOUND)
        participant = viewer is None or any(p["user_id"] == viewer for p in row.players)
        return match_result(row, participant=participant)

    async def mine(self, user_id: UUID, cursor: str | None, limit: int) -> Json:
        """Матчи пользователя, новые первыми (keyset ``finished_at``, ``match_id``)."""
        parts = decode_cursor(cursor)
        position = None
        if parts is not None:
            try:
                position = (datetime.fromisoformat(parts[0]), UUID(parts[1]))
            except (IndexError, ValueError) as exc:
                raise MatchInvalidError("неверный курсор", code="match.cursor_invalid") from exc
        rows = await self._records.mine(user_id, position, limit + 1)
        items = [
            {
                "match_id": str(r.match_id),
                "mode": {
                    "key": r.mode_key,
                    "version": r.mode_version,
                    "domain": r.domain,
                    "kind": r.format,
                    "title_key": r.title_key,
                    "clue_type": r.clue_type,
                    "answer_format": r.answer_format,
                    "ranked": r.ranked,
                },
                "finished_at": r.finished_at.isoformat(),
                "finish": r.finish,
                "players": r.players,
                "place": r.place,
                "score": r.score,
                "outcome": r.outcome,
                "my_finish": r.my_finish,
            }
            for r in rows[:limit]
        ]
        nxt = None
        if len(rows) > limit:
            last = rows[limit - 1]
            nxt = encode_cursor(last.finished_at.isoformat(), last.match_id)
        return {"items": items, "next_cursor": nxt}


class RatePlayer:
    """Похвала или жалоба после матча (``PlayerRated``, D14; обработка — этап 5)."""

    def __init__(
        self, records: MatchRecords, limiter: RateLimiter, clock: Clock, *, per_hour: int
    ) -> None:
        self._records = records
        self._limiter = limiter
        self._clock = clock
        self._per_hour = per_hour

    async def rate(
        self, user_id: UUID, match_id: UUID, *, to_user_id: UUID, kind: str, key: str
    ) -> None:
        """Один раз на матч, пару и вид; только участник об участнике.

        Raises:
            MatchNotFoundError: ``match.match_not_found``.
            MatchForbiddenError: ``match.not_participant``.
            MatchInvalidError: ``match.cannot_rate_self``.
            MatchConflictError: ``match.already_rated``.
        """
        await self._limiter.hit("rate", str(user_id), limit=self._per_hour, window_s=3600)
        if to_user_id == user_id:
            raise MatchInvalidError("нельзя оценить себя", code=errors.CANNOT_RATE_SELF)
        players = await self._records.participants(match_id)
        if players is None:
            raise MatchNotFoundError("матча нет или он идёт", code=errors.MATCH_NOT_FOUND)
        if user_id not in players or to_user_id not in players:
            raise MatchForbiddenError("оценка — только участникам", code=errors.NOT_PARTICIPANT)
        event = PlayerRated(
            aggregate_id=str(match_id),
            match_id=match_id,
            from_id=user_id,
            to_id=to_user_id,
            kind=kind,
            reason=None,
            at=self._clock.now(),
        )
        fingerprint = request_fingerprint(
            "match.rate", match_id, {"to_user_id": str(to_user_id), "kind": kind}
        )
        await self._records.rate(event, actor_id=user_id, key=key, fingerprint=fingerprint)
