"""Формат снимка комнаты в Redis (``schema_version``, ADR 0034, §13 плана).

Снимок — JSON всего агрегата ``Room`` (включая план и состояние раунда ядра). Новый
инстанс обязан читать снимок предыдущей версии: деплой посреди матча не должен ломать
комнату. Правило: новое поле — с значением по умолчанию при чтении; переименование или
смена смысла — ``SCHEMA_VERSION + 1`` и шаг в ``_UPGRADES`` (тест на сохранённом снимке
прошлой версии — ``tests/unit/test_codec.py``).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

from gv_game_kernel import (
    ContestRoundState,
    Guess,
    GuessKind,
    MatchVerdict,
    PlayerRound,
    RoundResult,
    RoundState,
)
from match_service.domain.model import (
    SCHEMA_VERSION,
    AnswerKey,
    ChatRules,
    Clue,
    LevelAsset,
    MatchFinish,
    MatchPlayer,
    MatchState,
    ModeInfo,
    Paused,
    PersonKey,
    Phase,
    PlannedRound,
    PlanOption,
    PlayerFinish,
    PlayerRoundResult,
    Presence,
    RoomSettings,
    RoomTemplate,
    RoundRecord,
    Seat,
    SettingsOptions,
    Standing,
)
from match_service.domain.room import Room

# Any: снимок — произвольный JSON, структура описана функциями ниже.
type Json = dict[str, Any]


class SnapshotError(ValueError):
    """Снимок не читается (неизвестная или будущая версия, повреждённые данные)."""


def _ids(values: Any) -> list[str]:
    return [str(v) for v in values]


def _tuple(values: Any) -> tuple[Any, ...] | None:
    return tuple(values) if values is not None else None


# ----------------------------------------------------------------------------- запись
def _clue(c: Clue | None) -> Json | None:
    if c is None:
        return None
    return {
        "level": c.level,
        "kind": c.kind,
        "url": c.url,
        "expires_at_ms": c.expires_at_ms,
        "mime": c.mime,
        "meta": c.meta,
    }


def _round_state(s: RoundState) -> Json:
    return {
        "level": s.level,
        "attempts_on_level": s.attempts_on_level,
        "outcome": s.outcome.value if s.outcome is not None else None,
        "partial_awarded_level": s.partial_awarded_level,
        "guesses": [
            {
                "kind": g.kind.value,
                "level": g.level,
                "verdict": g.verdict.value if g.verdict is not None else None,
                "display": g.display,
            }
            for g in s.guesses
        ],
    }


def _contest(c: ContestRoundState | None) -> Json | None:
    if c is None:
        return None
    return {
        "level": c.level,
        "level_opened_at_ms": c.level_opened_at_ms,
        "level_ends_at_ms": c.level_ends_at_ms,
        "level_locked": c.level_locked,
        "finished": c.finished,
        "players": {
            pid: {
                "active": p.active,
                "answered_at_ms": p.answered_at_ms,
                "elapsed_ms": p.elapsed_ms,
                "window_ms": p.window_ms,
                "state": _round_state(p.state),
            }
            for pid, p in c.players.items()
        },
    }


def _plan(r: PlannedRound) -> Json:
    a = r.answer
    return {
        "index": r.index,
        "answer": {
            "media_item_id": str(a.media_item_id),
            "domain": a.domain,
            "kind": a.kind,
            "title": a.title,
            "year": a.year,
            "aliases": list(a.aliases),
            "title_aliases": list(a.title_aliases),
            "persons": [
                {
                    "person_id": str(p.person_id),
                    "name": p.name,
                    "normalized": p.normalized,
                    "role": p.role,
                }
                for p in a.persons
            ],
            "partial": list(a.partial),
        },
        "levels": [{"level": x.level, "asset_set_id": str(x.asset_set_id)} for x in r.levels],
        "options": [
            {
                "option_id": o.option_id,
                "label_key": o.label_key,
                "media_item_id": str(o.media_item_id) if o.media_item_id else None,
                "title": o.title,
            }
            for o in r.options
        ],
        "reveal_asset_set_id": str(r.reveal_asset_set_id) if r.reveal_asset_set_id else None,
    }


def _match(m: MatchState | None) -> Json | None:
    if m is None:
        return None
    return {
        "match_id": str(m.match_id),
        "seed": m.seed,
        "mode_version": m.mode_version,
        "body": m.body,
        "plan": [_plan(r) for r in m.plan],
        "collections": [[str(c), v] for c, v in m.collections],
        "players": [
            {
                "user_id": str(p.user_id),
                "slot": p.slot,
                "score": p.score,
                "finish": p.finish.value if p.finish is not None else None,
                "disconnected_at_ms": p.disconnected_at_ms,
            }
            for p in m.players.values()
        ],
        "created_at_ms": m.created_at_ms,
        "round_index": m.round_index,
        "contest": _contest(m.contest),
        "clue": _clue(m.clue),
        "final_clue": _clue(m.final_clue),
        "records": [
            {
                "index": r.index,
                "media_item_id": str(r.media_item_id),
                "void": r.void,
                "results": [
                    {
                        "user_id": str(x.user_id),
                        "level": x.level,
                        "partial": x.partial,
                        "score": x.score,
                        "elapsed_ms": x.elapsed_ms,
                    }
                    for x in r.results
                ],
            }
            for r in m.records
        ],
        "finish": m.finish.value if m.finish is not None else None,
        "abort_reason": m.abort_reason,
        "paused": (
            {"phase": m.paused.phase.value, "remaining_ms": m.paused.remaining_ms}
            if m.paused is not None
            else None
        ),
        "started_at_ms": m.started_at_ms,
        "finished_at_ms": m.finished_at_ms,
        "standings": [
            {
                "user_id": str(s.user_id),
                "slot": s.slot,
                "score": s.score,
                "place": s.place,
                "outcome": s.outcome,
                "finish": s.finish.value,
            }
            for s in m.standings
        ],
        "started_written": m.started_written,
        "finished_written": m.finished_written,
        "grace_ms": m.grace_ms,
        "levels_count": m.levels_count,
        "attempts_per_level": m.attempts_per_level,
        "statuses": m.statuses,
        "clue_pending": m.clue_pending,
        "paused_at_ms": m.paused_at_ms,
    }


def _options(o: SettingsOptions | None) -> Json | None:
    if o is None:
        return None
    return {
        "rounds": o.rounds,
        "level_window_s": o.level_window_s,
        "answer_format": o.answer_format,
        "clue_type": o.clue_type,
        "players_max": o.players_max,
        "visibility": o.visibility,
    }


def encode(room: Room) -> Json:
    """Снимок комнаты (без несохраняемых ``changes``)."""
    t, s, m = room.template, room.settings, room.mode
    return {
        "schema_version": SCHEMA_VERSION,
        "code": room.code,
        "mode": {
            "key": m.key,
            "version": m.version,
            "domain": m.domain,
            "kind": m.kind,
            "title_key": m.title_key,
            "ranked": m.ranked,
            "ladder": m.ladder,
        },
        "template": {
            "players_min": t.players_min,
            "players_max": t.players_max,
            "rounds": t.rounds,
            "level_windows_s": list(t.level_windows_s),
            "answer_format": t.answer_format,
            "clue_type": t.clue_type,
            "visibility": t.visibility,
            "options": _options(t.options),
            "chat": {
                "emotes": list(t.chat.emotes),
                "phrases": list(t.chat.phrases),
                "free_text": t.chat.free_text,
            },
            "uses_collection": t.uses_collection,
            "max_collections": t.max_collections,
        },
        "settings": {
            "rounds": s.rounds,
            "level_window_s": s.level_window_s,
            "answer_format": s.answer_format,
            "clue_type": s.clue_type,
            "players_max": s.players_max,
            "visibility": s.visibility,
            "collection_ids": _ids(s.collection_ids),
        },
        "host_id": str(room.host_id),
        "seats": [
            {
                "user_id": str(x.user_id),
                "slot": x.slot,
                "nickname": x.nickname,
                "joined_at_ms": x.joined_at_ms,
                "presence": x.presence.value,
                "away": x.away,
                "ready": x.ready,
            }
            for x in room.seats
        ],
        "created_at_ms": room.created_at_ms,
        "activity_at_ms": room.activity_at_ms,
        "phase": room.phase.value,
        "phase_ends_at_ms": room.phase_ends_at_ms,
        "version": room.version,
        "phase_version": room.phase_version,
        "kicked": sorted(_ids(room.kicked)),
        "memory": _ids(room.memory),
        "match": _match(room.match),
        "last_match_id": str(room.last_match_id) if room.last_match_id else None,
        "rematch_votes": sorted(_ids(room.rematch_votes)),
        "rematch_code": room.rematch_code,
        "start_error": list(room.start_error) if room.start_error is not None else None,
        "forgotten": sorted(_ids(room.forgotten)),
        "close_reason": room.close_reason,
        "cursor": room.cursor,
        "ops": room.ops,
    }


def dumps(room: Room) -> str:
    """Снимок строкой JSON."""
    return json.dumps(encode(room), ensure_ascii=False, separators=(",", ":"))


# ----------------------------------------------------------------------------- чтение
def _uuid(v: Any) -> UUID:
    return UUID(str(v))


def _opt_uuid(v: Any) -> UUID | None:
    return UUID(str(v)) if v is not None else None


def _read_clue(d: Json | None) -> Clue | None:
    if d is None:
        return None
    return Clue(
        level=int(d["level"]),
        kind=str(d["kind"]),
        url=str(d["url"]),
        expires_at_ms=int(d["expires_at_ms"]),
        mime=str(d["mime"]),
        meta=dict(d.get("meta") or {}),
    )


def _read_round_state(d: Json) -> RoundState:
    return RoundState(
        level=int(d["level"]),
        attempts_on_level=int(d["attempts_on_level"]),
        outcome=RoundResult(d["outcome"]) if d.get("outcome") else None,
        partial_awarded_level=d.get("partial_awarded_level"),
        guesses=tuple(
            Guess(
                kind=GuessKind(g["kind"]),
                level=int(g["level"]),
                verdict=MatchVerdict(g["verdict"]) if g.get("verdict") else None,
                display=str(g.get("display", "")),
            )
            for g in d.get("guesses", [])
        ),
    )


def _read_contest(d: Json | None) -> ContestRoundState | None:
    if d is None:
        return None
    return ContestRoundState(
        level=int(d["level"]),
        level_opened_at_ms=d.get("level_opened_at_ms"),
        level_ends_at_ms=d.get("level_ends_at_ms"),
        level_locked=bool(d.get("level_locked", False)),
        finished=bool(d.get("finished", False)),
        players={
            pid: PlayerRound(
                state=_read_round_state(p["state"]),
                answered_at_ms=p.get("answered_at_ms"),
                active=bool(p.get("active", True)),
                # party_speed: без t и T очки после handoff падали бы до 1 (ревью, блокер 1).
                elapsed_ms=p.get("elapsed_ms"),
                window_ms=p.get("window_ms"),
            )
            for pid, p in d.get("players", {}).items()
        },
    )


def _read_plan(d: Json) -> PlannedRound:
    a = d["answer"]
    return PlannedRound(
        index=int(d["index"]),
        answer=AnswerKey(
            media_item_id=_uuid(a["media_item_id"]),
            domain=str(a["domain"]),
            kind=str(a["kind"]),
            title=str(a["title"]),
            year=a.get("year"),
            aliases=tuple(a.get("aliases", [])),
            title_aliases=tuple(a.get("title_aliases", [])),
            persons=tuple(
                PersonKey(
                    person_id=_uuid(p["person_id"]),
                    name=str(p["name"]),
                    normalized=str(p.get("normalized", "")),
                    role=str(p["role"]),
                )
                for p in a.get("persons", [])
            ),
            partial=tuple(a.get("partial", [])),
        ),
        levels=tuple(
            LevelAsset(level=int(x["level"]), asset_set_id=_uuid(x["asset_set_id"]))
            for x in d["levels"]
        ),
        options=tuple(
            PlanOption(
                option_id=str(o["option_id"]),
                label_key=o.get("label_key"),
                media_item_id=_opt_uuid(o.get("media_item_id")),
                title=o.get("title"),
            )
            for o in d.get("options", [])
        ),
        reveal_asset_set_id=_opt_uuid(d.get("reveal_asset_set_id")),
    )


def _read_match(d: Json | None) -> MatchState | None:
    if d is None:
        return None
    paused = d.get("paused")
    return MatchState(
        match_id=_uuid(d["match_id"]),
        seed=str(d["seed"]),
        mode_version=int(d["mode_version"]),
        body=dict(d["body"]),
        plan=tuple(_read_plan(r) for r in d["plan"]),
        collections=tuple((_uuid(c), int(v)) for c, v in d.get("collections", [])),
        players={
            _uuid(p["user_id"]): MatchPlayer(
                user_id=_uuid(p["user_id"]),
                slot=int(p["slot"]),
                score=int(p.get("score", 0)),
                finish=PlayerFinish(p["finish"]) if p.get("finish") else None,
                disconnected_at_ms=p.get("disconnected_at_ms"),
            )
            for p in d["players"]
        },
        created_at_ms=int(d["created_at_ms"]),
        round_index=int(d.get("round_index", 0)),
        contest=_read_contest(d.get("contest")),
        clue=_read_clue(d.get("clue")),
        final_clue=_read_clue(d.get("final_clue")),
        records=[
            RoundRecord(
                index=int(r["index"]),
                media_item_id=_uuid(r["media_item_id"]),
                void=bool(r.get("void", False)),
                results=tuple(
                    PlayerRoundResult(
                        user_id=_uuid(x["user_id"]),
                        level=x.get("level"),
                        partial=bool(x.get("partial", False)),
                        score=int(x.get("score", 0)),
                        elapsed_ms=x.get("elapsed_ms"),
                    )
                    for x in r.get("results", [])
                ),
            )
            for r in d.get("records", [])
        ],
        finish=MatchFinish(d["finish"]) if d.get("finish") else None,
        abort_reason=d.get("abort_reason"),
        paused=(
            Paused(phase=Phase(paused["phase"]), remaining_ms=paused.get("remaining_ms"))
            if paused
            else None
        ),
        started_at_ms=d.get("started_at_ms"),
        finished_at_ms=d.get("finished_at_ms"),
        standings=tuple(
            Standing(
                user_id=_uuid(s["user_id"]),
                slot=int(s["slot"]),
                score=int(s["score"]),
                place=int(s["place"]),
                outcome=s.get("outcome"),
                finish=PlayerFinish(s["finish"]),
            )
            for s in d.get("standings", [])
        ),
        started_written=bool(d.get("started_written", False)),
        finished_written=bool(d.get("finished_written", False)),
        grace_ms=int(d.get("grace_ms", 0)),
        levels_count=int(d.get("levels_count", 1)),
        attempts_per_level=int(d.get("attempts_per_level", 1)),
        statuses=dict(d.get("statuses", {})),
        clue_pending=bool(d.get("clue_pending", False)),
        paused_at_ms=d.get("paused_at_ms"),
    )


def _read_options(d: Json | None) -> SettingsOptions | None:
    if d is None:
        return None
    return SettingsOptions(
        rounds=_tuple(d.get("rounds")),
        level_window_s=_tuple(d.get("level_window_s")),
        answer_format=_tuple(d.get("answer_format")),
        clue_type=_tuple(d.get("clue_type")),
        players_max=_tuple(d.get("players_max")),
        visibility=_tuple(d.get("visibility")),
    )


def _read_ops(raw: Json) -> dict[str, dict[str, Json]]:
    """Окно ``op_id``: ``user → op → итог``; прежний плоский формат ``"user:op" → ответ``
    (до окна на игрока) читается с переводом в компактный итог."""
    ops: dict[str, dict[str, Json]] = {}
    for key, value in raw.items():
        if isinstance(value, dict) and "status" not in value:
            ops[key] = {str(op): dict(v) for op, v in value.items()}
            continue
        user, _, op = str(key).partition(":")
        if op and isinstance(value, dict):
            compact = {k: value[k] for k in ("status", "code", "details") if k in value}
            ops.setdefault(user, {})[op] = compact
    return ops


_UPGRADES: dict[int, Callable[[Json], Json]] = {}
"""Шаги ``версия → версия + 1`` (пока одна версия формата)."""


def decode(data: Json) -> Room:
    """Комната из снимка текущей или прошлой версии.

    Raises:
        SnapshotError: версия новее известной или данные повреждены.
    """
    version = int(data.get("schema_version", 1))
    if version > SCHEMA_VERSION:
        raise SnapshotError(f"снимок версии {version} новее известной {SCHEMA_VERSION}")
    while version < SCHEMA_VERSION:
        data = _UPGRADES[version](data)
        version += 1
    try:
        return _decode(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise SnapshotError("снимок комнаты повреждён") from exc


def _decode(d: Json) -> Room:
    m, t, s = d["mode"], d["template"], d["settings"]
    chat = t.get("chat") or {}
    return Room(
        code=str(d["code"]),
        mode=ModeInfo(
            key=str(m["key"]),
            version=int(m["version"]),
            domain=str(m["domain"]),
            kind=str(m["kind"]),
            title_key=str(m["title_key"]),
            ranked=bool(m.get("ranked", False)),
            ladder=m.get("ladder"),
        ),
        template=RoomTemplate(
            players_min=int(t["players_min"]),
            players_max=int(t["players_max"]),
            rounds=int(t["rounds"]),
            level_windows_s=tuple(t["level_windows_s"]),
            answer_format=str(t["answer_format"]),
            clue_type=str(t["clue_type"]),
            visibility=str(t["visibility"]),
            options=_read_options(t.get("options")),
            chat=ChatRules(
                emotes=tuple(chat.get("emotes", [])),
                phrases=tuple(chat.get("phrases", [])),
                free_text=bool(chat.get("free_text", False)),
            ),
            uses_collection=bool(t.get("uses_collection", False)),
            max_collections=int(t.get("max_collections", 0)),
        ),
        settings=RoomSettings(
            rounds=int(s["rounds"]),
            level_window_s=s.get("level_window_s"),
            answer_format=str(s["answer_format"]),
            clue_type=str(s["clue_type"]),
            players_max=int(s["players_max"]),
            visibility=str(s["visibility"]),
            collection_ids=tuple(_uuid(c) for c in s.get("collection_ids", [])),
        ),
        host_id=_uuid(d["host_id"]),
        seats=[
            Seat(
                user_id=_uuid(x["user_id"]),
                slot=int(x["slot"]),
                nickname=str(x.get("nickname", "")),
                joined_at_ms=int(x["joined_at_ms"]),
                presence=Presence(x.get("presence", "online")),
                away=bool(x.get("away", False)),
                ready=bool(x.get("ready", False)),
            )
            for x in d["seats"]
        ],
        created_at_ms=int(d["created_at_ms"]),
        activity_at_ms=int(d.get("activity_at_ms") or d["created_at_ms"]),
        phase=Phase(d["phase"]),
        phase_ends_at_ms=d.get("phase_ends_at_ms"),
        version=int(d["version"]),
        phase_version=int(d.get("phase_version") or d["version"]),
        kicked={_uuid(k) for k in d.get("kicked", [])},
        memory=[_uuid(x) for x in d.get("memory", [])],
        match=_read_match(d.get("match")),
        last_match_id=_opt_uuid(d.get("last_match_id")),
        rematch_votes={_uuid(v) for v in d.get("rematch_votes", [])},
        rematch_code=d.get("rematch_code"),
        start_error=(
            (str(d["start_error"][0]), int(d["start_error"][1])) if d.get("start_error") else None
        ),
        forgotten={_uuid(u) for u in d.get("forgotten", [])},
        close_reason=d.get("close_reason"),
        cursor=str(d.get("cursor", "0-0")),
        ops=_read_ops(d.get("ops") or {}),
    )


def loads(raw: str | bytes) -> Room:
    """Комната из строки снимка."""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SnapshotError("снимок комнаты — не JSON") from exc
    if not isinstance(data, dict):
        raise SnapshotError("снимок комнаты — не объект")
    return decode(data)
