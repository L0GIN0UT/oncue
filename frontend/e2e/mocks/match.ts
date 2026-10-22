/**
 * Мок онлайна этапа 4a по контрактам `contracts/openapi/match.json`,
 * `contracts/realtime/room.message.v1.json` и экранам bff (`/v1/pages/online`, `/match/{id}`).
 *
 * Комната — упрощённый владелец: фазы по таймерам (`advance()` раз в 100 мс), версия на
 * каждое изменение, журнал сообщений канала. Канал в e2e — не Centrifugo, а лента
 * `GET /api/match/__e2e/rooms/{code}/feed?after=N`, которую читает подменённый транспорт
 * (e2e/realtime.ts). Сценарии: пропуск версии в ленте, «упавший» канал, отказ чата.
 */
import type { components } from "../../src/shared/api/schema/match";
import { ITEMS, type MockItem, type MockWorld } from "./world";

type S = components["schemas"];
type Phase = S["RoomView"]["phase"];
type Status = NonNullable<S["MatchPlayerView"]["status"]>;

export type MatchScenario = {
  /** Следующее опубликованное сообщение в ленту не попадёт (дыра в версиях → ресинк). */
  dropNextMessage?: boolean;
  /** Лента канала отвечает 503 (Centrifugo недоступен) — клиент живёт на tick. */
  feedDown?: boolean;
  /** Слова, на которые чат отвечает 422 `match.chat_rejected` (denylist). */
  chatDeny?: string[];
  /** Следующая команда ответит 202 `CommandAccepted` (владелец не успел), применившись. */
  acceptOnce?: boolean;
  /** Без tick дольше — «Отключён». */
  disconnectAfterMs?: number;
  countdownMs?: number;
  revealMs?: number;
};

type ModeDef = {
  ref: S["ModeRef"];
  min: number;
  max: number;
  rounds: number;
  windowS: number;
  levels: number;
  attempts: number;
  /** Очки за угадывание по уровням (дуэль) — у party очки за скорость. */
  points: number[];
  /** Первый угадавший закрывает уровень для всех (`race`). */
  race: boolean;
  chat: S["ChatOptions"];
  options: S["SettingsOptions"] | null;
};

const EMOTES = ["match.emotes.wow", "match.emotes.gg", "match.emotes.fire"];
const PHRASES = ["match.phrases.good_luck", "match.phrases.well_played"];

const ref = (
  key: string,
  domain: "music" | "film",
  kind: "duel" | "party" | "lobby",
  clue: string,
): S["ModeRef"] => ({
  key,
  version: 1,
  domain,
  kind,
  title_key: `modes.${key}.title`,
  clue_type: clue,
  answer_format: "classic",
  ranked: false,
});

export const ONLINE_MODES: ModeDef[] = [
  {
    ref: ref("music.duel.classic", "music", "duel", "audio"),
    min: 2,
    max: 2,
    rounds: 2,
    windowS: 20,
    levels: 2,
    attempts: 3,
    points: [12, 6],
    race: true,
    chat: { emotes: EMOTES, phrases: PHRASES, free_text: false },
    options: null,
  },
  {
    ref: ref("film.party.frame", "film", "party", "frame"),
    min: 2,
    max: 6,
    rounds: 2,
    windowS: 25,
    levels: 1,
    attempts: 1,
    points: [10],
    race: false,
    chat: { emotes: EMOTES, phrases: PHRASES, free_text: false },
    options: null,
  },
  {
    ref: ref("music.lobby.classic", "music", "lobby", "audio"),
    min: 2,
    max: 6,
    rounds: 2,
    windowS: 20,
    levels: 1,
    attempts: 1,
    points: [10],
    race: false,
    chat: { emotes: EMOTES, phrases: PHRASES, free_text: true },
    options: {
      rounds: [2, 3, 5],
      level_window_s: [10, 20, 30],
      players_max: [2, 4, 6],
      visibility: ["public", "private"],
      answer_format: null,
      clue_type: null,
      max_collections: null,
    },
  },
];

type PlayerRound = {
  attemptsLeft: number;
  guesses: S["OwnGuess"][];
  status: Status;
  points: number;
  level: number | null;
};

type MockMatch = {
  id: string;
  items: MockItem[];
  roundIndex: number;
  level: number;
  levelOpenedAt: number;
  clue: S["LevelClue"] | null;
  per: Map<string, PlayerRound>;
  rounds: S["ResultRound"][];
  forfeits: Set<string>;
  startedAt: string;
};

type MockRoom = {
  code: string;
  def: ModeDef;
  settings: S["RoomSettings"];
  hostId: string;
  players: (S["PlayerSlot"] & { lastSeen: number })[];
  kicked: Set<string>;
  phase: Phase;
  phaseEndsAt: number | null;
  version: number;
  log: { message: Record<string, unknown>; dropped: boolean }[];
  match: MockMatch | null;
  matchId: string | null;
  memory: Set<string>;
  rematch: Set<string>;
  createdAt: string;
};

const CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

export class MatchWorld {
  rooms = new Map<string, MockRoom>();
  playerRoom = new Map<string, string>();
  results = new Map<string, S["MatchResult"]>();
  ops = new Map<string, { status: number; body: unknown }>();
  ratings = new Set<string>();
  scenario: MatchScenario = {};
  private seq = 0;
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(private readonly world: MockWorld) {}

  reset(): void {
    this.rooms.clear();
    this.playerRoom.clear();
    this.results.clear();
    this.ops.clear();
    this.ratings.clear();
    this.scenario = {};
    this.seq = 0;
  }

  /** Часы владельца: фазы по таймерам, присутствие по tick. */
  startClock(): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      for (const room of this.rooms.values()) this.advance(room);
    }, 100);
    this.timer.unref?.();
  }

  now(): number {
    return this.world.now().getTime();
  }

  nickname(userId: string): string {
    return this.world.users.find((u) => u.id === userId)?.nickname ?? "player";
  }

  mode(key: string): ModeDef | undefined {
    return ONLINE_MODES.find((m) => m.ref.key === key);
  }

  newCode(): string {
    for (;;) {
      this.seq += 1;
      let n = this.seq * 7_919 + 104_729;
      let code = "";
      for (let i = 0; i < 8; i++) {
        code += CODE_ALPHABET[n % CODE_ALPHABET.length];
        n = Math.floor(n / CODE_ALPHABET.length) + i * 13;
      }
      if (!this.rooms.has(code)) return code;
    }
  }

  // ------------------------------------------------------------------ комнаты

  create(userId: string, def: ModeDef, override: S["SettingsOverride"] | null): MockRoom {
    const code = this.newCode();
    const room: MockRoom = {
      code,
      def,
      settings: {
        rounds: override?.rounds ?? def.rounds,
        level_window_s: override?.level_window_s ?? def.windowS,
        answer_format: def.ref.answer_format,
        clue_type: def.ref.clue_type,
        players_max: override?.players_max ?? def.max,
        visibility: override?.visibility ?? "private",
        collection_ids: [],
      },
      hostId: userId,
      players: [],
      kicked: new Set(),
      phase: "lobby",
      phaseEndsAt: null,
      version: 1,
      log: [],
      match: null,
      matchId: null,
      memory: new Set(),
      rematch: new Set(),
      createdAt: new Date(this.now()).toISOString(),
    };
    this.rooms.set(code, room);
    this.addPlayer(room, userId);
    return room;
  }

  addPlayer(room: MockRoom, userId: string): void {
    const slot = Math.max(-1, ...room.players.map((p) => p.slot)) + 1;
    room.players.push({
      user_id: userId,
      nickname: this.nickname(userId),
      slot,
      is_host: room.hostId === userId,
      ready: false,
      presence: "online",
      away: false,
      score: 0,
      lastSeen: this.now(),
    });
    this.playerRoom.set(userId, room.code);
  }

  member(room: MockRoom, userId: string) {
    return room.players.find((p) => p.user_id === userId) ?? null;
  }

  // ------------------------------------------------------------------ виды

  roomView(room: MockRoom): S["RoomView"] {
    return {
      code: room.code,
      version: room.version,
      phase: room.phase,
      phase_ends_at: room.phaseEndsAt ? new Date(room.phaseEndsAt).toISOString() : null,
      server_now: new Date(this.now()).toISOString(),
      host_id: room.hostId,
      match_id: room.matchId,
      memory_size: room.memory.size,
      mode: room.def.ref,
      options: room.def.options,
      players: room.players.map(({ lastSeen: _, ...p }) => ({
        ...p,
        is_host: p.user_id === room.hostId,
      })),
      players_min: room.def.min,
      settings: room.settings,
      visibility: room.settings.visibility,
      chat: room.def.chat,
    };
  }

  matchView(room: MockRoom): S["MatchView"] | null {
    const m = room.match;
    if (!m) return null;
    const item = m.items[m.roundIndex];
    const revealing = room.phase === "reveal" || room.phase === "summary";
    return {
      match_id: m.id,
      round_index: m.roundIndex,
      rounds_total: room.settings.rounds,
      level: room.phase === "level_open" || room.phase === "level_closed" ? m.level : null,
      levels_count: room.def.levels,
      clue: room.phase === "level_open" ? m.clue : null,
      reveal: revealing && item ? this.revealOf(item) : null,
      players: room.players.map((p) => {
        const r = m.per.get(p.user_id);
        return {
          user_id: p.user_id,
          score: p.score,
          status:
            room.phase === "level_open" || room.phase === "level_closed"
              ? (r?.status ?? null)
              : null,
          round_points: revealing ? (r?.points ?? 0) : null,
        };
      }),
    };
  }

  meView(room: MockRoom, userId: string): S["MyRoundView"] | null {
    const r = room.match?.per.get(userId);
    if (!room.match || !r) return null;
    return {
      round_index: room.match.roundIndex,
      attempts_left: r.attemptsLeft,
      guesses: r.guesses,
      status: r.status,
    };
  }

  state(room: MockRoom, userId: string): S["RoomState"] {
    return {
      room: this.roomView(room),
      match: this.matchView(room),
      me: this.meView(room, userId),
    };
  }

  revealOf(item: MockItem) {
    return {
      media_item_id: item.id,
      title: item.title,
      year: item.year,
      persons: [{ name: item.person.name, role: item.person.role }],
      final_clue: null,
    };
  }

  // ------------------------------------------------------------------ публикация

  publish(room: MockRoom, kind: string, delta: Record<string, unknown>): void {
    room.version += 1;
    const dropped = !!this.scenario.dropNextMessage;
    this.scenario.dropNextMessage = false;
    room.log.push({
      dropped,
      message: {
        version: room.version,
        kind,
        server_now: new Date(this.now()).toISOString(),
        delta,
      },
    });
    if (room.log.length > 200) room.log.splice(0, room.log.length - 200);
  }

  /** Полный снимок лобби: версия в снимке — та, что получит сообщение. */
  publishLobby(room: MockRoom): void {
    this.publish(room, "lobby", { room: this.roomView({ ...room, version: room.version + 1 }) });
  }

  feed(room: MockRoom, after: number) {
    if (after <= 0) return { messages: [], last: room.version };
    return {
      messages: room.log
        .filter((e) => !e.dropped && (e.message.version as number) > after)
        .map((e) => e.message),
      last: room.version,
    };
  }

  playerDelta(room: MockRoom, userId: string): Record<string, unknown> {
    const p = this.member(room, userId)!;
    const r = room.match?.per.get(userId);
    return {
      user_id: userId,
      presence: p.presence,
      ready: p.ready,
      away: p.away,
      score: p.score,
      status:
        room.match && (room.phase === "level_open" || room.phase === "level_closed")
          ? (r?.status ?? null)
          : null,
    };
  }

  // ------------------------------------------------------------------ матч

  start(room: MockRoom): void {
    const pool = ITEMS.filter((i) => i.domain === room.def.ref.domain && !room.memory.has(i.id));
    const items = pool.slice(0, room.settings.rounds);
    const id = `0192b000-0000-7000-8000-${String(this.rooms.size * 1000 + room.version).padStart(12, "0")}`;
    room.match = {
      id,
      items,
      roundIndex: 0,
      level: 1,
      levelOpenedAt: 0,
      clue: null,
      per: new Map(),
      rounds: [],
      forfeits: new Set(),
      startedAt: new Date(this.now()).toISOString(),
    };
    room.matchId = id;
    for (const p of room.players) p.score = 0;
    this.setPhase(room, "countdown", this.scenario.countdownMs ?? 1_000);
    this.publish(room, "phase", {
      phase: "countdown",
      phase_ends_at: new Date(room.phaseEndsAt!).toISOString(),
      round_index: null,
      level: null,
      rounds_total: room.settings.rounds,
    });
  }

  private setPhase(room: MockRoom, phase: Phase, durationMs: number | null): void {
    room.phase = phase;
    room.phaseEndsAt = durationMs === null ? null : this.now() + durationMs;
  }

  private openLevel(room: MockRoom, newRound: boolean): void {
    const m = room.match!;
    const item = m.items[m.roundIndex]!;
    if (newRound) {
      m.per = new Map(
        room.players
          .filter((p) => p.presence !== "left" && !m.forfeits.has(p.user_id))
          .map((p) => [
            p.user_id,
            {
              attemptsLeft: room.def.attempts,
              guesses: [],
              status: "thinking" as Status,
              points: 0,
              level: null,
            },
          ]),
      );
    } else {
      for (const r of m.per.values()) {
        if (r.status !== "guessed") {
          r.status = "thinking";
          r.attemptsLeft = room.def.attempts;
        }
      }
    }
    const seed = ITEMS.indexOf(item) + 1;
    const kind = room.def.ref.domain === "music" ? "audio" : "image";
    m.clue = {
      level: m.level,
      kind,
      url: this.world.assetUrl(
        kind,
        kind === "audio" ? 2 * m.level : 0,
        kind === "image" ? 18 - 6 * m.level : 0,
        seed,
      ),
      expires_at: new Date(this.now() + 60_000).toISOString(),
      mime: kind === "audio" ? "audio/wav" : "image/svg+xml",
      meta: kind === "audio" ? { seconds: 2 * m.level } : { blur_px: 18 - 6 * m.level },
    };
    m.levelOpenedAt = this.now();
    this.setPhase(room, "level_open", room.settings.level_window_s * 1000);
    this.publish(room, "level_opened", {
      round_index: m.roundIndex,
      level: m.level,
      phase_ends_at: new Date(room.phaseEndsAt!).toISOString(),
      clue: m.clue,
    });
  }

  private closeLevel(room: MockRoom): void {
    const m = room.match!;
    for (const r of m.per.values())
      if (r.status === "thinking" || r.status === "answered") r.status = "missed";
    this.setPhase(room, "level_closed", 400);
    this.publish(room, "level_closed", {
      round_index: m.roundIndex,
      level: m.level,
      players: [...m.per].map(([user_id, r]) => ({ user_id, status: r.status, level: r.level })),
    });
  }

  private reveal(room: MockRoom): void {
    const m = room.match!;
    const item = m.items[m.roundIndex]!;
    m.rounds.push({
      index: m.roundIndex,
      media_item_id: item.id,
      void: false,
      results: [...m.per].map(([user_id, r]) => ({
        user_id,
        level: r.level,
        partial: false,
        score: r.points,
        elapsed_ms: null,
      })),
    });
    room.memory.add(item.id);
    this.setPhase(room, "reveal", this.scenario.revealMs ?? 1_500);
    this.publish(room, "round_reveal", {
      round_index: m.roundIndex,
      ...this.revealOf(item),
      results: room.players.map((p) => {
        const r = m.per.get(p.user_id);
        return {
          user_id: p.user_id,
          level: r?.level ?? null,
          partial: false,
          score: r?.points ?? 0,
          total: p.score,
        };
      }),
    });
    this.publish(room, "phase", {
      phase: "reveal",
      phase_ends_at: new Date(room.phaseEndsAt!).toISOString(),
      round_index: m.roundIndex,
      level: null,
    });
  }

  finish(room: MockRoom, finish: S["MatchResult"]["finish"] = "completed"): void {
    const m = room.match!;
    const active = room.players.filter((p) => !m.forfeits.has(p.user_id) && p.presence !== "left");
    const ranked = [...room.players].sort((a, b) => {
      const ao = active.includes(a) ? 0 : 1;
      const bo = active.includes(b) ? 0 : 1;
      return ao - bo || b.score - a.score;
    });
    const placements = ranked.map((p) => {
      const gone = !active.includes(p);
      const place = gone
        ? ranked.indexOf(p) + 1
        : 1 + ranked.filter((q) => active.includes(q) && q.score > p.score).length;
      const duel = room.def.ref.kind === "duel";
      const tied = active.filter((q) => q.score === p.score).length > 1 && !gone;
      return {
        user_id: p.user_id,
        nickname: p.nickname,
        slot: p.slot,
        place,
        score: p.score,
        outcome: duel ? (gone ? "loss" : tied ? "draw" : place === 1 ? "win" : "loss") : null,
        finish: m.forfeits.has(p.user_id)
          ? "forfeit"
          : p.presence === "left"
            ? "left"
            : "completed",
      } as S["ResultPlayer"];
    });
    this.results.set(m.id, {
      match_id: m.id,
      room_code: room.code,
      mode: room.def.ref,
      started_at: m.startedAt,
      finished_at: new Date(this.now()).toISOString(),
      finish,
      players: placements,
      rounds: m.rounds,
      collection_ids: [],
    });
    this.setPhase(room, "summary", null);
    this.publish(room, "summary", {
      match_id: m.id,
      placements: placements.map(({ nickname: _, slot: __, ...p }) => p),
    });
    this.publish(room, "phase", {
      phase: "summary",
      phase_ends_at: null,
      round_index: null,
      level: null,
    });
  }

  /** Переходы по времени и присутствие (вызывается часами мока). */
  advance(room: MockRoom): void {
    const now = this.now();
    const grace = this.scenario.disconnectAfterMs ?? 12_000;
    for (const p of room.players) {
      if (p.presence === "online" && now - p.lastSeen > grace) {
        p.presence = "disconnected";
        this.publish(room, "player", this.playerDelta(room, p.user_id));
      }
    }
    if (!room.match || room.phaseEndsAt === null || now < room.phaseEndsAt) return;
    const m = room.match;
    switch (room.phase) {
      case "countdown":
        m.roundIndex = 0;
        m.level = 1;
        this.openLevel(room, true);
        return;
      case "level_open":
        this.closeLevel(room);
        return;
      case "level_closed": {
        const anyGuessed = [...m.per.values()].some((r) => r.status === "guessed");
        if (!anyGuessed && m.level < room.def.levels) {
          m.level += 1;
          this.openLevel(room, false);
        } else this.reveal(room);
        return;
      }
      case "reveal":
        if (m.roundIndex + 1 < Math.min(room.settings.rounds, m.items.length)) {
          m.roundIndex += 1;
          m.level = 1;
          this.openLevel(room, true);
        } else this.finish(room);
        return;
    }
  }

  /** Ход игрока: попытка уровня. */
  guess(
    room: MockRoom,
    userId: string,
    answer: Record<string, unknown>,
  ): S["GuessResult"] | { error: string } {
    const m = room.match!;
    const r = m.per.get(userId);
    if (room.phase !== "level_open" || !r) return { error: "match.phase_mismatch" };
    if (r.status === "guessed" || r.attemptsLeft <= 0) return { error: "match.level_locked" };
    const item = m.items[m.roundIndex]!;
    const text = typeof answer.text === "string" ? answer.text.trim().toLowerCase() : null;
    const exact =
      answer.media_item_id === item.id || (text !== null && text === item.title.toLowerCase());
    const display =
      typeof answer.media_item_id === "string"
        ? (ITEMS.find((i) => i.id === answer.media_item_id)?.title ?? "?")
        : String(answer.text ?? "");
    r.attemptsLeft -= 1;
    r.guesses.push({
      level: m.level,
      kind: text !== null ? "text" : "item",
      display,
      result: exact ? "exact" : "miss",
    });
    const player = this.member(room, userId)!;
    if (exact) {
      r.status = "guessed";
      r.level = m.level;
      const elapsed = (this.now() - m.levelOpenedAt) / 1000;
      r.points = room.def.race
        ? (room.def.points[m.level - 1] ?? 1)
        : Math.max(
            1,
            Math.round(room.def.points[0]! * (1 - elapsed / room.settings.level_window_s)),
          );
      player.score += r.points;
    } else if (r.attemptsLeft <= 0) {
      r.status = "missed";
    } else if (!room.def.race) {
      r.status = "answered";
    }
    this.publish(room, "player", { ...this.playerDelta(room, userId), score: null });
    const everyoneDone = [...m.per.values()].every(
      (x) => x.status === "guessed" || x.status === "missed",
    );
    if ((room.def.race && exact) || everyoneDone) this.closeLevel(room);
    return {
      version: room.version,
      result: exact ? "exact" : "miss",
      attempts_left: r.attemptsLeft,
      level_closed: r.status === "guessed" || r.status === "missed",
      me: this.meView(room, userId)!,
    };
  }

  skip(room: MockRoom, userId: string): boolean {
    const r = room.match?.per.get(userId);
    if (room.phase !== "level_open" || !r) return false;
    r.guesses.push({ level: room.match!.level, kind: "skip", display: "", result: null });
    r.status = "missed";
    r.attemptsLeft = 0;
    this.publish(room, "player", this.playerDelta(room, userId));
    if ([...room.match!.per.values()].every((x) => x.status === "guessed" || x.status === "missed"))
      this.closeLevel(room);
    return true;
  }

  forfeit(room: MockRoom, userId: string): void {
    const m = room.match!;
    m.forfeits.add(userId);
    m.per.delete(userId);
    this.publish(room, "player", this.playerDelta(room, userId));
    const left = room.players.filter((p) => !m.forfeits.has(p.user_id) && p.presence !== "left");
    if (room.def.ref.kind === "duel" || left.length < 2) this.finish(room, "forfeit");
  }

  /** «Мои матчи». */
  myMatches(userId: string): S["MyMatchCard"][] {
    return [...this.results.values()]
      .filter((r) => r.players.some((p) => p.user_id === userId))
      .reverse()
      .map((r) => {
        const me = r.players.find((p) => p.user_id === userId)!;
        return {
          match_id: r.match_id,
          mode: r.mode,
          finished_at: r.finished_at,
          finish: r.finish,
          players: r.players.length,
          place: me.place,
          score: me.score,
          outcome: me.outcome,
          my_finish: me.finish,
        };
      });
  }

  openRooms(): S["OpenRoomCard"][] {
    return [...this.rooms.values()]
      .filter(
        (r) =>
          r.phase === "lobby" &&
          r.settings.visibility === "public" &&
          r.players.length < r.settings.players_max,
      )
      .reverse()
      .map((r) => ({
        code: r.code,
        created_at: r.createdAt,
        host_nickname: this.nickname(r.hostId),
        mode: r.def.ref,
        players: r.players.length,
        players_max: r.settings.players_max,
        settings: r.settings,
      }));
  }

  itemCard(id: string) {
    const i = ITEMS.find((x) => x.id === id)!;
    return {
      media_item_id: i.id,
      domain: i.domain,
      kind: i.domain === "music" ? "track" : "film",
      title: i.title,
      original_title: null,
      year: i.year,
      genres: i.genres,
      persons: [{ name: i.person.name, role: i.person.role }],
      external_links: [],
    };
  }
}
