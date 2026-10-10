import { describe, expect, it } from "vitest";
import type { RoundOut } from "@/shared/api/types";
import { roundOutcome, skipGainSeconds, tapeWeights } from "./levels";

const timed = [0.5, 1, 2, 4, 8, 15].map((seconds, i) => ({ index: i + 1, hint: { seconds } }));
const frames = [48, 32, 20].map((blur_px, i) => ({ index: i + 1, hint: { blur_px } }));

describe("разметка уровней по подсказкам конфига", () => {
  it("загадка со временем — отрезки по приросту длительности", () => {
    expect(tapeWeights(timed, 6)).toEqual([0.5, 0.5, 1, 2, 4, 7]);
    expect(skipGainSeconds(timed, 1)).toBe(0.5);
    expect(skipGainSeconds(timed, 4)).toBe(4);
    expect(skipGainSeconds(timed, 6)).toBeNull();
  });

  it("без времени (кадры) или до загрузки режима — равные отрезки, без секунд у «Пропустить»", () => {
    expect(tapeWeights(frames, 3)).toEqual([1, 1, 1]);
    expect(tapeWeights([], 4)).toEqual([1, 1, 1, 1]);
    expect(skipGainSeconds(frames, 1)).toBeNull();
  });

  it("исход раунда для индикатора", () => {
    const r = (p: Partial<RoundOut>): RoundOut => ({
      index: 0,
      status: "finished",
      level: 1,
      levels_count: 6,
      guesses: [],
      ...p,
    });
    expect(
      roundOutcome(r({ guesses: [{ kind: "item", display: "a", result: "exact" }], points: 6 })),
    ).toBe("exact");
    expect(
      roundOutcome(r({ guesses: [{ kind: "item", display: "a", result: "partial" }], points: 2 })),
    ).toBe("partial");
    expect(roundOutcome(r({ points: 0 }))).toBe("miss");
    expect(roundOutcome(r({ status: "void" }))).toBe("void");
    expect(roundOutcome(r({ status: "active" }))).toBe("active");
  });
});
