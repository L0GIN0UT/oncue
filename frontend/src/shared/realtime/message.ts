/**
 * Сообщения канала комнаты `room:{code}` — `contracts/realtime/room.message.v1.json`.
 *
 * Конверт `{version, kind, server_now, delta}`. Неизвестный `kind` не ошибка: версия
 * засчитывается, дельта игнорируется (расширение `kind` совместимо). Битый конверт
 * отбрасывается целиком — состояние догонит ресинк по версии или tick.
 */
import type {
  LevelClue,
  PlayerStatus,
  RoomPhase,
  RoomView,
  RoundRevealView,
} from "@/shared/api/types";

export type Outcome = "win" | "loss" | "draw";
export type PlayerFinish = "completed" | "forfeit" | "left" | "tech_win" | "tech_loss";

export type LobbyDelta = { room: RoomView };
export type PlayerDelta = {
  user_id: string;
  presence: "online" | "disconnected" | "left";
  ready?: boolean;
  away?: boolean;
  score?: number | null;
  status?: PlayerStatus | null;
};
export type PhaseDelta = {
  phase: RoomPhase;
  phase_ends_at: string | null;
  round_index: number | null;
  level: number | null;
  rounds_total?: number;
};
export type LevelOpenedDelta = {
  round_index: number;
  level: number;
  phase_ends_at: string;
  clue: LevelClue | null;
};
export type LevelClosedDelta = {
  round_index: number;
  level: number;
  players: { user_id: string; status: PlayerStatus; level: number | null }[];
};
export type RoundRevealDelta = Omit<RoundRevealView, "final_clue"> & {
  round_index: number;
  final_clue: LevelClue | null;
  results: {
    user_id: string;
    level: number | null;
    partial: boolean;
    score: number;
    total: number;
  }[];
};
export type SummaryDelta = {
  match_id: string;
  placements: {
    user_id: string;
    place: number;
    score: number;
    outcome: Outcome | null;
    finish: PlayerFinish;
  }[];
};
export type ChatDelta = { message_id: string; user_id: string; text: string; at: string };
export type EmoteDelta = {
  user_id: string;
  at: string;
  emote?: string | null;
  phrase?: string | null;
};
/** Комната закрыта; при реванше (`reason: rematch`) — код новой комнаты. */
export type ClosedDelta = { reason: string; rematch_code?: string | null };

type Envelope<K extends string, D> = { version: number; kind: K; server_now: string; delta: D };

export type KnownRoomMessage =
  | Envelope<"lobby", LobbyDelta>
  | Envelope<"player", PlayerDelta>
  | Envelope<"phase", PhaseDelta>
  | Envelope<"level_opened", LevelOpenedDelta>
  | Envelope<"level_closed", LevelClosedDelta>
  | Envelope<"round_reveal", RoundRevealDelta>
  | Envelope<"summary", SummaryDelta>
  | Envelope<"chat", ChatDelta>
  | Envelope<"emote", EmoteDelta>
  | Envelope<"closed", ClosedDelta>;

/** Сообщение канала: известный вид или будущий (`kind` вне списка — только версия). */
export type RoomMessage = KnownRoomMessage | Envelope<string & {}, Record<string, unknown>>;

export const KNOWN_KINDS = new Set<string>([
  "lobby",
  "player",
  "phase",
  "level_opened",
  "level_closed",
  "round_reveal",
  "summary",
  "chat",
  "emote",
  "closed",
]);

export function isKnownMessage(m: RoomMessage): m is KnownRoomMessage {
  return KNOWN_KINDS.has(m.kind);
}

/** Разобрать публикацию канала; `null` — конверт не по контракту. */
export function parseRoomMessage(raw: unknown): RoomMessage | null {
  if (!raw || typeof raw !== "object") return null;
  const m = raw as Record<string, unknown>;
  if (
    !Number.isInteger(m.version) ||
    (m.version as number) < 1 ||
    typeof m.kind !== "string" ||
    !/^[a-z][a-z0-9_]*$/.test(m.kind) ||
    typeof m.server_now !== "string" ||
    !m.delta ||
    typeof m.delta !== "object"
  ) {
    return null;
  }
  return m as unknown as RoomMessage;
}
