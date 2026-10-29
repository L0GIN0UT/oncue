"""Виды комнаты и матча для зрителя (R9, §12 п. 1 — античит).

Виды строятся из снимка без ядра (процесс ``api`` не владеет комнатой). Правила:

* до фазы ``reveal`` нет ``media_item_id``, названия, ключей ответа и плана — ``reveal``
  появляется только в фазе ``reveal`` текущего раунда;
* ссылка уровня — только у открытого уровня;
* чужие ответы — только статусом; свои попытки и варианты ``pick`` — только в ``me``
  (ответ команды и ``GET /state``), в канал не идут (D9).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from match_service.application.ports import Json
from match_service.domain.model import Change, Clue, MatchState, Phase, Presence
from match_service.domain.room import Room


def iso(ms: int | None) -> str | None:
    """Миллисекунды Unix → ISO 8601 (UTC)."""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def to_ms(moment: datetime) -> int:
    """Момент → миллисекунды Unix."""
    return int(moment.timestamp() * 1000)


def clue_json(clue: Clue | None) -> Json | None:
    """``LevelClue``."""
    if clue is None:
        return None
    return {
        "level": clue.level,
        "kind": clue.kind,
        "url": clue.url,
        "expires_at": iso(clue.expires_at_ms),
        "mime": clue.mime,
        "meta": dict(clue.meta),
    }


def mode_ref(room: Room) -> Json:
    """``ModeRef`` (действующие формат и загадка комнаты)."""
    m = room.mode
    return {
        "key": m.key,
        "version": m.version,
        "domain": m.domain,
        "kind": m.kind,
        "title_key": m.title_key,
        "clue_type": room.settings.clue_type,
        "answer_format": room.settings.answer_format,
        "ranked": m.ranked,
    }


def settings_json(room: Room) -> Json:
    """``RoomSettings``."""
    s = room.settings
    return {
        "rounds": s.rounds,
        "level_window_s": s.level_window_s or room.template.level_windows_s[0],
        "answer_format": s.answer_format,
        "clue_type": s.clue_type,
        "players_max": s.players_max,
        "visibility": s.visibility,
        "collection_ids": [str(c) for c in s.collection_ids],
        "name": s.name,
    }


def _score(room: Room, user_id: UUID) -> int:
    match = room.match
    player = match.players.get(user_id) if match is not None else None
    return player.score if player is not None else 0


def room_view(room: Room, now_ms: int) -> Json:
    """``RoomView`` — то же для всех участников (и дельта ``lobby``)."""
    t = room.template
    options = None
    if t.options is not None:
        o = t.options
        options = {
            "rounds": list(o.rounds) if o.rounds else None,
            "level_window_s": list(o.level_window_s) if o.level_window_s else None,
            "answer_format": list(o.answer_format) if o.answer_format else None,
            "clue_type": list(o.clue_type) if o.clue_type else None,
            "players_max": list(o.players_max) if o.players_max else None,
            "visibility": list(o.visibility) if o.visibility else None,
            "max_collections": t.max_collections if t.uses_collection else None,
        }
    match_id = room.match.match_id if room.match is not None else room.last_match_id
    return {
        "code": room.code,
        "visibility": room.settings.visibility,
        "mode": mode_ref(room),
        "host_id": str(room.host_id),
        "players": [
            {
                "slot": s.slot,
                "user_id": str(s.user_id),
                "nickname": s.nickname,
                "is_host": s.user_id == room.host_id,
                "presence": s.presence.value,
                "away": s.away,
                "ready": s.ready,
                "score": _score(room, s.user_id),
            }
            for s in sorted(room.seats, key=lambda s: s.slot)
        ],
        "players_min": t.players_min,
        "settings": settings_json(room),
        "options": options,
        "chat": {
            "emotes": list(t.chat.emotes),
            "phrases": list(t.chat.phrases),
            "free_text": t.chat.free_text,
        },
        "phase": room.phase.value,
        "phase_ends_at": iso(room.phase_ends_at_ms),
        "match_id": str(match_id) if match_id is not None else None,
        "memory_size": len(room.memory),
        "rematch_code": room.rematch_code,
        "start_error": (
            {"code": room.start_error[0], "at": iso(room.start_error[1])}
            if room.start_error is not None
            else None
        ),
        "version": room.version,
        "server_now": iso(now_ms),
    }


def _reveal(match: MatchState) -> Json:
    plan = match.current
    key = plan.answer
    return {
        "media_item_id": str(key.media_item_id),
        "title": key.title,
        "year": key.year,
        "persons": [{"name": p.name, "role": p.role} for p in key.persons],
        "final_clue": clue_json(match.final_clue),
    }


def match_view(room: Room) -> Json | None:
    """``MatchView`` — для любого участника, без плана и ключей ответа."""
    match = room.match
    if match is None or room.phase in (Phase.LOBBY, Phase.CLOSED):
        return None
    level_open = room.phase is Phase.LEVEL_OPEN and match.contest is not None
    revealing = room.phase is Phase.REVEAL and bool(match.records)
    record = match.records[-1] if revealing else None
    points = {r.user_id: r.score for r in record.results} if record is not None else {}
    return {
        "match_id": str(match.match_id),
        "round_index": min(match.round_index, match.rounds_total - 1),
        "rounds_total": match.rounds_total,
        "level": match.contest.level if level_open and match.contest is not None else None,
        "levels_count": match.levels_count,
        "clue": clue_json(match.clue) if level_open else None,
        "players": [
            {
                "user_id": str(p.user_id),
                "status": match.statuses.get(str(p.user_id)) if level_open else None,
                "score": p.score,
                "round_points": points.get(p.user_id) if record is not None else None,
            }
            for p in sorted(match.players.values(), key=lambda p: p.slot)
        ],
        "reveal": _reveal(match) if revealing else None,
    }


def my_round_view(room: Room, user_id: UUID) -> Json | None:
    """``MyRoundView`` — только своё (ответ команды и ``GET /state``)."""
    match = room.match
    if match is None or match.contest is None or room.phase in (Phase.LOBBY, Phase.CLOSED):
        return None
    if room.phase is Phase.SUMMARY or user_id not in match.players:
        return None
    contest = match.contest
    pr = contest.players.get(str(user_id))
    guesses: list[Json] = []
    attempts_left = 0
    if pr is not None:
        guesses = [
            {
                "level": g.level,
                "kind": g.kind.value,
                "display": g.display,
                "result": g.verdict.value if g.verdict is not None else None,
            }
            for g in pr.state.guesses
        ]
        if (
            room.phase is Phase.LEVEL_OPEN
            and pr.active
            and not pr.state.finished
            and pr.state.level == contest.level
        ):
            attempts_left = max(0, match.attempts_per_level - pr.state.attempts_on_level)
    options: list[Json] = []
    if room.phase is Phase.LEVEL_OPEN:
        options = [
            {"option_id": o.option_id, "label_key": o.label_key, "title": o.title}
            for o in match.current.options
        ]
    return {
        "round_index": min(match.round_index, match.rounds_total - 1),
        "status": match.statuses.get(str(user_id)) if room.phase is Phase.LEVEL_OPEN else None,
        "attempts_left": attempts_left,
        "guesses": guesses,
        "options": options,
    }


def room_state(room: Room, viewer: UUID, now_ms: int) -> Json:
    """``RoomState`` для участника."""
    return {
        "room": room_view(room, now_ms),
        "match": match_view(room),
        "me": my_round_view(room, viewer),
    }


# ----------------------------------------------------------------------------- канал
def _player_delta(room: Room, user_id: str) -> Json | None:
    uid = UUID(user_id)
    seat = room.seat_of(uid)
    if seat is None:
        return None
    match = room.match
    in_round = match is not None and room.phase is Phase.LEVEL_OPEN
    return {
        "user_id": user_id,
        "presence": seat.presence.value,
        "ready": seat.ready,
        "away": seat.away,
        "score": _score(room, uid) if match is not None else None,
        "status": match.statuses.get(user_id) if in_round and match is not None else None,
    }


def _round_reveal(room: Room, index: int) -> Json | None:
    match = room.match
    if match is None:
        return None
    record = next((r for r in match.records if r.index == index), None)
    if record is None:
        return None
    key = match.plan[index].answer
    totals = {p.user_id: p.score for p in match.players.values()}
    return {
        "round_index": index,
        "media_item_id": str(key.media_item_id),
        "title": key.title,
        "year": key.year,
        "persons": [{"name": p.name, "role": p.role} for p in key.persons],
        "final_clue": clue_json(match.final_clue) if match.round_index == index else None,
        "results": [
            {
                "user_id": str(r.user_id),
                "level": r.level,
                "partial": r.partial,
                "score": r.score,
                "total": totals.get(r.user_id, 0),
            }
            for r in record.results
        ],
    }


def _summary(room: Room) -> Json | None:
    match = room.match
    if match is None or not match.standings:
        return None
    return {
        "match_id": str(match.match_id),
        "placements": [
            {
                "user_id": str(s.user_id),
                "place": s.place,
                "score": s.score,
                "outcome": s.outcome,
                "finish": s.finish.value,
            }
            for s in match.standings
        ],
    }


def _delta(room: Room, change: Change, now_ms: int) -> Json | None:  # noqa: PLR0911 — диспетчер видов
    data: dict[str, Any] = change.data  # Any: примитивы JSON изменения
    kind = change.kind
    if kind == "lobby":
        return {"room": room_view(room, now_ms)}
    if kind == "player":
        return _player_delta(room, str(data["user_id"]))
    if kind == "phase":
        return {
            "phase": data["phase"],
            "phase_ends_at": iso(data.get("phase_ends_at_ms")),
            "round_index": data.get("round_index"),
            "level": data.get("level"),
            **({"rounds_total": data["rounds_total"]} if data.get("rounds_total") else {}),
        }
    if kind == "level_opened":
        clue = data.get("clue")
        return {
            "round_index": data["round_index"],
            "level": data["level"],
            "phase_ends_at": iso(data.get("phase_ends_at_ms")),
            "clue": clue_json(clue if isinstance(clue, Clue) else None),
        }
    if kind == "level_closed":
        return {
            "round_index": data["round_index"],
            "level": data["level"],
            "players": list(data.get("players", [])),
        }
    if kind == "round_reveal":
        return _round_reveal(room, int(data["round_index"]))
    if kind == "summary":
        return _summary(room)
    if kind in ("chat", "emote", "closed"):
        return dict(data)
    return None


def messages(room: Room, changes: list[Change], now_ms: int) -> list[Json]:
    """Сообщения канала ``room:{code}`` по изменениям (``room.message.v1``)."""
    out: list[Json] = []
    for change in changes:
        kind, delta = change.kind, _delta(room, change, now_ms)
        if delta is None:
            # Версия уже занята: без сообщения клиент увидел бы пропуск и ресинкнулся.
            kind, delta = "lobby", {"room": room_view(room, now_ms)}
        out.append(
            {"version": change.version, "kind": kind, "server_now": iso(now_ms), "delta": delta}
        )
    return out


def seat_presence(room: Room, user_id: UUID) -> Presence | None:
    """Присутствие игрока или ``None`` (не в комнате)."""
    seat = room.seat_of(user_id)
    return seat.presence if seat is not None else None
