import { describe, expect, it } from "vitest";
import type { MoveResult, RoundOut, SessionSnapshot } from "@/shared/api/types";
import {
  gameReducer,
  initialGameState,
  readyForResults,
  visibleRound,
  type GameState,
} from "./state";

const round = (index: number, patch: Partial<RoundOut> = {}): RoundOut => ({
  index,
  status: index === 0 ? "active" : "pending",
  level: 1,
  levels_count: 6,
  guesses: [],
  points: null,
  reveal: null,
  ...patch,
});

const snap = (patch: Partial<SessionSnapshot> = {}): SessionSnapshot => ({
  id: "s1",
  version: 0,
  status: "active",
  mode: { key: "music.daily.classic", version: 1, domain: "music", clue_type: "audio_intro" },
  day: "2026-10-10",
  score: 0,
  max_score: 30,
  current_round: 0,
  rounds: [round(0), round(1)],
  ...patch,
});

const withSnap = (s: SessionSnapshot): GameState =>
  gameReducer(initialGameState, { type: "snapshot", snapshot: s });

describe("gameReducer", () => {
  it("игнорирует устаревший снимок", () => {
    const state = withSnap(snap({ version: 3 }));
    const next = gameReducer(state, {
      type: "snapshot",
      snapshot: snap({ version: 2, score: 99 }),
    });
    expect(next.snapshot?.version).toBe(3);
  });

  it("после хода, завершившего раунд, держит ревил до «Далее»", () => {
    const finished = round(0, {
      status: "finished",
      points: 5,
      guesses: [{ kind: "item", display: "X", result: "exact" }],
    });
    const result: MoveResult = {
      result: "exact",
      round: finished,
      session: snap({
        version: 1,
        current_round: 1,
        score: 5,
        rounds: [finished, round(1, { status: "active" })],
      }),
    };
    let state = gameReducer(withSnap(snap()), { type: "move_started" });
    expect(state.busy).toBe(true);
    state = gameReducer(state, { type: "move_succeeded", result });
    expect(state.busy).toBe(false);
    expect(state.revealRound).toBe(0);
    expect(visibleRound(state)?.index).toBe(0);
    state = gameReducer(state, { type: "continue" });
    expect(visibleRound(state)?.index).toBe(1);
  });

  it("промах оставляет раунд активным без ревила", () => {
    const missed = round(0, {
      level: 2,
      guesses: [{ kind: "text", display: "x", result: "miss" }],
    });
    const state = gameReducer(withSnap(snap()), {
      type: "move_succeeded",
      result: {
        result: "miss",
        round: missed,
        session: snap({ version: 1, rounds: [missed, round(1)] }),
      },
    });
    expect(state.revealRound).toBeNull();
    expect(state.lastOutcome).toBe("miss");
    expect(visibleRound(state)?.level).toBe(2);
  });

  it("ресинк, в котором активный раунд уже завершён, открывает его ревил", () => {
    const finished = round(0, { status: "finished", points: 0 });
    const state = gameReducer(withSnap(snap()), {
      type: "snapshot",
      snapshot: snap({
        version: 1,
        current_round: 1,
        rounds: [finished, round(1, { status: "active" })],
      }),
    });
    expect(state.revealRound).toBe(0);
  });

  it("итог — только после закрытия ревила последнего раунда", () => {
    const last = round(1, { status: "finished", points: 6 });
    let state = gameReducer(
      withSnap(
        snap({
          current_round: 1,
          rounds: [round(0, { status: "finished" }), round(1, { status: "active" })],
        }),
      ),
      {
        type: "move_succeeded",
        result: {
          result: "exact",
          round: last,
          session: snap({
            version: 1,
            status: "finished",
            current_round: 1,
            rounds: [round(0, { status: "finished" }), last],
          }),
        },
      },
    );
    expect(readyForResults(state)).toBe(false);
    state = gameReducer(state, { type: "continue" });
    expect(readyForResults(state)).toBe(true);
  });

  it("ошибка хода снимает блокировку и показывает уведомление", () => {
    let state = gameReducer(withSnap(snap()), { type: "move_started" });
    state = gameReducer(state, { type: "move_failed", notice: "rate_limited" });
    expect(state).toMatchObject({ busy: false, notice: "rate_limited" });
    expect(gameReducer(state, { type: "dismiss_notice" }).notice).toBeNull();
  });
});
