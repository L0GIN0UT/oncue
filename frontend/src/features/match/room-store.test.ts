import { describe, expect, it } from "vitest";
import type { MatchView, RoomState, RoomView } from "@/shared/api/types";
import type { RoomMessage } from "@/shared/realtime";
import { initialRoomStore, roomReducer, type RoomStore } from "./room-store";

const A = "00000000-0000-4000-8000-00000000000a";
const B = "00000000-0000-4000-8000-00000000000b";
const NOW = "2026-10-22T10:00:00Z";

function room(patch: Partial<RoomView> = {}): RoomView {
  return {
    code: "K7MQ2XWP",
    version: 10,
    phase: "level_open",
    phase_ends_at: "2026-10-22T10:00:20Z",
    server_now: NOW,
    host_id: A,
    memory_size: 0,
    players_min: 2,
    visibility: "private",
    chat: { emotes: ["match.emotes.gg"], phrases: [], free_text: false },
    mode: {
      key: "music.duel.classic",
      version: 1,
      domain: "music",
      kind: "duel",
      title_key: "modes.music.duel.classic.title",
      clue_type: "audio",
      answer_format: "classic",
      ranked: false,
    },
    settings: {
      rounds: 3,
      level_window_s: 20,
      answer_format: "classic",
      clue_type: "audio",
      players_max: 2,
      visibility: "private",
    },
    players: [
      {
        user_id: A,
        nickname: "a",
        slot: 0,
        is_host: true,
        ready: true,
        presence: "online",
        away: false,
        score: 0,
      },
      {
        user_id: B,
        nickname: "b",
        slot: 1,
        is_host: false,
        ready: true,
        presence: "online",
        away: false,
        score: 0,
      },
    ],
    ...patch,
  };
}

function match(patch: Partial<MatchView> = {}): MatchView {
  return {
    match_id: "m-1",
    round_index: 0,
    rounds_total: 3,
    level: 1,
    levels_count: 2,
    clue: null,
    reveal: null,
    players: [
      { user_id: A, score: 0, status: "thinking" },
      { user_id: B, score: 0, status: "thinking" },
    ],
    ...patch,
  };
}

const snapshot = (state: Partial<RoomState> = {}): RoomStore =>
  roomReducer(initialRoomStore, {
    type: "snapshot",
    state: {
      room: room(),
      match: match(),
      me: { round_index: 0, attempts_left: 3, guesses: [], status: "thinking" },
      ...state,
    },
  });

const send = (store: RoomStore, version: number, kind: string, delta: object) =>
  roomReducer(store, {
    type: "message",
    message: { version, kind, server_now: NOW, delta } as RoomMessage,
  });

describe("roomReducer", () => {
  it("player: присутствие и статус без текста ответа", () => {
    const s = send(snapshot(), 11, "player", {
      user_id: B,
      presence: "disconnected",
      status: "answered",
    });
    expect(s.state!.room.version).toBe(11);
    expect(s.state!.room.players[1]!.presence).toBe("disconnected");
    expect(s.state!.match!.players[1]!.status).toBe("answered");
  });

  it("level_closed: статусы игроков, фаза", () => {
    const s = send(snapshot(), 11, "level_closed", {
      round_index: 0,
      level: 1,
      players: [
        { user_id: A, status: "guessed", level: 1 },
        { user_id: B, status: "missed", level: null },
      ],
    });
    expect(s.state!.room.phase).toBe("level_closed");
    expect(s.state!.match!.players.map((p) => p.status)).toEqual(["guessed", "missed"]);
  });

  it("level_opened следующего раунда — новая загадка, статусы сброшены, me устарел", () => {
    const s = send(snapshot(), 11, "level_opened", {
      round_index: 1,
      level: 1,
      phase_ends_at: "2026-10-22T10:01:00Z",
      clue: {
        level: 1,
        kind: "audio",
        url: "/media/a/x.wav",
        expires_at: NOW,
        mime: "audio/wav",
        meta: {},
      },
    });
    expect(s.state!.match!.round_index).toBe(1);
    expect(s.state!.match!.clue!.url).toBe("/media/a/x.wav");
    expect(s.state!.room.phase_ends_at).toBe("2026-10-22T10:01:00Z");
    expect(s.state!.match!.players.every((p) => p.status === "thinking")).toBe(true);
    expect(s.meStale).toBe(true);
  });

  it("round_reveal: ответ и очки раунда; phase после ревила его убирает", () => {
    let s = send(snapshot(), 11, "round_reveal", {
      round_index: 0,
      media_item_id: "i-1",
      title: "Группа крови",
      year: 1988,
      persons: [],
      final_clue: null,
      results: [
        { user_id: A, level: 1, partial: false, score: 12, total: 12 },
        { user_id: B, level: null, partial: false, score: 0, total: 0 },
      ],
    });
    expect(s.reveal!.title).toBe("Группа крови");
    expect(s.state!.match!.players[0]).toMatchObject({ score: 12, round_points: 12 });
    expect(s.state!.room.players[0]!.score).toBe(12);
    s = send(s, 12, "phase", { phase: "reveal", phase_ends_at: null, round_index: 0, level: null });
    expect(s.reveal).not.toBeNull();
    s = send(s, 13, "phase", {
      phase: "level_open",
      phase_ends_at: null,
      round_index: 1,
      level: 1,
    });
    expect(s.reveal).toBeNull();
    expect(s.state!.match!.reveal).toBeNull();
    expect(s.meStale).toBe(true);
  });

  it("chat и emote копятся в ленте; неизвестный kind не меняет состояние", () => {
    let s = send(snapshot(), 11, "emote", { user_id: B, at: NOW, emote: "match.emotes.gg" });
    s = send(s, 12, "chat", { message_id: "c1", user_id: A, text: "<b>привет</b>", at: NOW });
    expect(s.feed.map((f) => f.kind)).toEqual(["emote", "chat"]);
    const same = send(s, 13, "future_kind", { x: 1 });
    expect(same).toBe(s);
  });

  it("summary и closed", () => {
    let s = send(snapshot(), 11, "summary", {
      match_id: "m-1",
      placements: [{ user_id: A, place: 1, score: 12, outcome: "win", finish: "completed" }],
    });
    expect(s.state!.room.phase).toBe("summary");
    expect(s.summary!.placements[0]!.outcome).toBe("win");
    s = send(s, 12, "closed", { reason: "idle" });
    expect(s.state!.room.phase).toBe("closed");
    expect(s.closedReason).toBe("idle");
  });

  it("lobby — полный снимок комнаты", () => {
    const s = send(snapshot({ match: null, me: null }), 11, "lobby", {
      room: room({ phase: "lobby", version: 11, players: [] }),
    });
    expect(s.state!.room.players).toEqual([]);
    expect(s.state!.room.version).toBe(11);
  });

  it("guess: своё состояние раунда из ответа команды", () => {
    const s = roomReducer(snapshot(), {
      type: "guess",
      result: {
        version: 11,
        result: "miss",
        attempts_left: 2,
        level_closed: false,
        me: {
          round_index: 0,
          attempts_left: 2,
          status: "thinking",
          guesses: [{ level: 1, kind: "text", display: "Кукушка", result: "miss" }],
        },
      },
    });
    expect(s.state!.me!.attempts_left).toBe(2);
    expect(s.state!.me!.guesses).toHaveLength(1);
  });

  it("снимок после ресинка снимает meStale", () => {
    const s = { ...snapshot(), meStale: true };
    const next = roomReducer(s, { type: "snapshot", state: s.state! });
    expect(next.meStale).toBe(false);
  });
});
