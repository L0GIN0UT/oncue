/**
 * Состояние комнаты на клиенте: снимок `RoomState` + дельты канала по порядку версий
 * (порядок гарантирует `RoomLink`). Редьюсер чистый: время и сеть — снаружи.
 *
 * Личное (`me`: свои попытки, варианты) в канал не идёт (ADR 0035): при смене раунда `me`
 * помечается устаревшим (`meStale`), экран берёт снимок `GET /state`.
 */
import type {
  GuessResult,
  MatchPlayerView,
  MatchView,
  MyRoundView,
  RoomState,
  RoomView,
} from "@/shared/api/types";
import {
  isKnownMessage,
  type ChatDelta,
  type EmoteDelta,
  type KnownRoomMessage,
  type RoomMessage,
  type RoundRevealDelta,
  type SummaryDelta,
} from "@/shared/realtime";

export type FeedItem =
  | ({ kind: "chat"; version: number } & ChatDelta)
  | ({ kind: "emote"; version: number } & EmoteDelta);

export type RoomStore = {
  state: RoomState | null;
  /** Чат и эмоции (последние {@link FEED_LIMIT}). */
  feed: FeedItem[];
  /** Ревил текущего раунда — с результатами раунда (очки, уровень угадывания). */
  reveal: RoundRevealDelta | null;
  summary: SummaryDelta | null;
  closedReason: string | null;
  /** `me` относится к другому раунду — нужен снимок. */
  meStale: boolean;
};

export const FEED_LIMIT = 50;

export const initialRoomStore: RoomStore = {
  state: null,
  feed: [],
  reveal: null,
  summary: null,
  closedReason: null,
  meStale: false,
};

export type RoomAction =
  | { type: "snapshot"; state: RoomState }
  | { type: "message"; message: RoomMessage }
  | { type: "guess"; result: GuessResult };

export function roomReducer(store: RoomStore, action: RoomAction): RoomStore {
  switch (action.type) {
    case "snapshot":
      return fromSnapshot(store, action.state);
    case "message":
      return isKnownMessage(action.message) ? applyMessage(store, action.message) : store;
    case "guess": {
      const state = store.state;
      if (!state?.match || state.match.round_index !== action.result.me.round_index) return store;
      return { ...store, state: { ...state, me: action.result.me } };
    }
  }
}

function fromSnapshot(store: RoomStore, state: RoomState): RoomStore {
  const reveal = state.match?.reveal
    ? store.reveal?.round_index === state.match.round_index
      ? store.reveal
      : revealFromView(state.match)
    : null;
  return {
    ...store,
    state,
    reveal,
    meStale: false,
    closedReason: state.room.phase === "closed" ? (store.closedReason ?? "closed") : null,
    summary: state.room.phase === "summary" || state.room.phase === "closed" ? store.summary : null,
  };
}

/** Ревил из снимка (после ресинка): результаты раунда — из очков игроков. */
function revealFromView(match: MatchView): RoundRevealDelta | null {
  const r = match.reveal;
  if (!r) return null;
  return {
    ...r,
    round_index: match.round_index,
    results: match.players.map((p) => ({
      user_id: p.user_id,
      level: null,
      partial: false,
      score: p.round_points ?? 0,
      total: p.score,
    })),
  };
}

function withRoom(store: RoomStore, version: number, patch: Partial<RoomView>): RoomStore {
  const state = store.state;
  if (!state) return store;
  return { ...store, state: { ...state, room: { ...state.room, ...patch, version } } };
}

function withMatch(store: RoomStore, patch: (m: MatchView) => MatchView): RoomStore {
  const state = store.state;
  if (!state?.match) return store;
  return { ...store, state: { ...state, match: patch(state.match) } };
}

function patchPlayers(
  players: MatchPlayerView[],
  update: (p: MatchPlayerView) => MatchPlayerView,
): MatchPlayerView[] {
  return players.map(update);
}

function pushFeed(store: RoomStore, item: FeedItem): RoomStore {
  return { ...store, feed: [...store.feed, item].slice(-FEED_LIMIT) };
}

function applyMessage(store: RoomStore, m: KnownRoomMessage): RoomStore {
  const v = m.version;
  const room = { server_now: m.server_now };
  switch (m.kind) {
    case "lobby":
      if (!store.state) return store;
      return {
        ...store,
        state: { ...store.state, room: { ...m.delta.room, version: v } },
      };

    case "player": {
      const d = m.delta;
      let next = withRoom(store, v, {
        ...room,
        players: (store.state?.room.players ?? []).map((p) =>
          p.user_id === d.user_id
            ? {
                ...p,
                presence: d.presence,
                ...(d.ready !== undefined ? { ready: d.ready } : {}),
                ...(d.away !== undefined ? { away: d.away } : {}),
                ...(typeof d.score === "number" ? { score: d.score } : {}),
              }
            : p,
        ),
      });
      next = withMatch(next, (match) => ({
        ...match,
        players: patchPlayers(match.players, (p) =>
          p.user_id === d.user_id
            ? {
                ...p,
                ...(d.status !== undefined ? { status: d.status } : {}),
                ...(typeof d.score === "number" ? { score: d.score } : {}),
              }
            : p,
        ),
      }));
      return next;
    }

    case "phase": {
      const d = m.delta;
      let next = withRoom(store, v, {
        ...room,
        phase: d.phase,
        phase_ends_at: d.phase_ends_at,
      });
      const roundChanged =
        d.round_index !== null &&
        next.state?.match &&
        d.round_index !== next.state.match.round_index;
      next = withMatch(next, (match) => ({
        ...match,
        round_index: d.round_index ?? match.round_index,
        level: d.level,
        rounds_total: d.rounds_total ?? match.rounds_total,
        ...(roundChanged ? { clue: null } : {}),
        reveal: d.phase === "reveal" ? match.reveal : null,
        players: roundChanged
          ? match.players.map((p) => ({ ...p, status: null, round_points: null }))
          : match.players,
      }));
      if (d.phase !== "reveal" && d.phase !== "summary" && d.phase !== "closed")
        next = { ...next, reveal: null };
      // Новый раунд или матч, которого в снимке ещё нет (после отсчёта), — нужен снимок.
      const me = next.state?.me;
      if (d.round_index !== null && (!me || me.round_index !== d.round_index))
        next = { ...next, meStale: true };
      return next;
    }

    case "level_opened": {
      const d = m.delta;
      let next = withRoom(store, v, {
        ...room,
        phase: "level_open",
        phase_ends_at: d.phase_ends_at,
      });
      next = withMatch(next, (match) => ({
        ...match,
        round_index: d.round_index,
        level: d.level,
        clue: d.clue,
        reveal: null,
        players: match.players.map((p) => ({
          ...p,
          status:
            p.status === "guessed" && match.round_index === d.round_index ? p.status : "thinking",
        })),
      }));
      const me = next.state?.me;
      if (!me || me.round_index !== d.round_index) next = { ...next, meStale: true };
      return { ...next, reveal: null };
    }

    case "level_closed": {
      const d = m.delta;
      const byUser = new Map(d.players.map((p) => [p.user_id, p]));
      const next = withRoom(store, v, { ...room, phase: "level_closed" });
      return withMatch(next, (match) => ({
        ...match,
        players: match.players.map((p) => {
          const u = byUser.get(p.user_id);
          return u ? { ...p, status: u.status } : p;
        }),
      }));
    }

    case "round_reveal": {
      const d = m.delta;
      const byUser = new Map(d.results.map((r) => [r.user_id, r]));
      const next = withRoom(store, v, room);
      const withReveal = withMatch(next, (match) => ({
        ...match,
        reveal: {
          media_item_id: d.media_item_id,
          title: d.title,
          year: d.year,
          persons: d.persons,
          final_clue: d.final_clue,
        },
        players: match.players.map((p) => {
          const r = byUser.get(p.user_id);
          return r ? { ...p, score: r.total, round_points: r.score } : p;
        }),
      }));
      const players = withReveal.state?.room.players.map((p) => {
        const r = byUser.get(p.user_id);
        return r ? { ...p, score: r.total } : p;
      });
      return {
        ...withRoom(withReveal, v, players ? { players } : {}),
        reveal: d,
      };
    }

    case "summary":
      return { ...withRoom(store, v, { ...room, phase: "summary" }), summary: m.delta };

    case "chat":
      return pushFeed(withRoom(store, v, room), { kind: "chat", version: v, ...m.delta });

    case "emote":
      return pushFeed(withRoom(store, v, room), { kind: "emote", version: v, ...m.delta });

    case "closed":
      return {
        ...withRoom(store, v, { ...room, phase: "closed", phase_ends_at: null }),
        closedReason: m.delta.reason,
      };
  }
}

/** Мой слот в комнате по `user_id`; `null` — не участник (или ещё не знаем, кто я). */
export function mySlot(room: RoomView | undefined, userId: string | null) {
  if (!room || !userId) return null;
  return room.players.find((p) => p.user_id === userId) ?? null;
}

export type { MyRoundView };
