import { describe, expect, it } from "vitest";
import type { RoundOut } from "@/shared/api/types";
import { needsLevelClue, optionForKey, pickLayout } from "./pick";
import { bracketStages, pairInStage, stageLabel } from "../bracket";

const clue = {
  kind: "image" as const,
  url: "/media/a/1.webp",
  expires_at: "x",
  mime: "image/webp",
};
const pair = {
  shown: [{ option_id: null, title: "Брат", year: 1997, metric: 8.1, clue }],
  options: [
    { option_id: "higher", label_key: "game.options.higher", title: null },
    { option_id: "lower", label_key: "game.options.lower", title: null },
  ],
};
const bracket = {
  shown: [
    { option_id: "a", title: "Солярис", year: 1972, metric: null, clue },
    { option_id: "b", title: "Сталкер", year: 1979, metric: null, clue },
  ],
  options: [
    { option_id: "a", label_key: null, title: "Солярис" },
    { option_id: "b", label_key: null, title: "Сталкер" },
  ],
};

describe("pickLayout", () => {
  it("пара: опорная единица отдельно, варианты — подписи без единиц", () => {
    const layout = pickLayout(pair);
    expect(layout.reference.map((s) => s.title)).toEqual(["Брат"]);
    expect(layout.cards.map((c) => [c.option.option_id, c.shown])).toEqual([
      ["higher", null],
      ["lower", null],
    ]);
    expect(needsLevelClue(pair)).toBe(true);
  });

  it("сетка: каждый вариант — своя открытая единица, загадка уровня не нужна", () => {
    const layout = pickLayout(bracket);
    expect(layout.reference).toEqual([]);
    expect(layout.cards.map((c) => c.shown?.title)).toEqual(["Солярис", "Сталкер"]);
    expect(needsLevelClue(bracket)).toBe(false);
  });

  it("классический раунд (вариантов нет) — загадка уровня нужна", () => {
    expect(needsLevelClue({ shown: [], options: [] })).toBe(true);
    expect(needsLevelClue({} as RoundOut)).toBe(true);
  });
});

describe("optionForKey", () => {
  const options = pair.options;
  it("цифры — по порядку, стрелки — первый и второй", () => {
    expect(optionForKey("1", options)).toBe("higher");
    expect(optionForKey("2", options)).toBe("lower");
    expect(optionForKey("3", options)).toBeNull();
    expect(optionForKey("ArrowLeft", options)).toBe("higher");
    expect(optionForKey("ArrowRight", options)).toBe("lower");
    expect(optionForKey("Enter", options)).toBeNull();
  });

  it("стрелки только при двух вариантах", () => {
    const three = [...options, { option_id: "same", label_key: null, title: null }];
    expect(optionForKey("ArrowLeft", three)).toBeNull();
    expect(optionForKey("3", three)).toBe("same");
  });
});

describe("туры сетки", () => {
  it("из размера первого тура: 32 → 16 … финал", () => {
    expect(bracketStages(32)).toEqual([32, 16, 8, 4, 2]);
    expect(bracketStages(8)).toEqual([8, 4, 2]);
    expect(bracketStages(null)).toEqual([]);
  });

  it("подписи туров", () => {
    expect(stageLabel(32)).toEqual({ key: "game.bracket.fraction", denominator: 16 });
    expect(stageLabel(4)).toEqual({ key: "game.bracket.semifinal" });
    expect(stageLabel(2)).toEqual({ key: "game.bracket.final" });
  });

  it("номер пары в туре — по раундам того же тура до текущего", () => {
    const r = (index: number, stage: number | null): RoundOut => ({
      index,
      status: "finished",
      level: 1,
      levels_count: 1,
      guesses: [],
      progress: stage ? { lives_left: null, streak: null, stage } : null,
    });
    const rounds = [r(0, 4), r(1, 4), r(2, 2)];
    expect(pairInStage(rounds, rounds[1]!)).toEqual({ pair: 2, pairs: 2 });
    expect(pairInStage(rounds, rounds[2]!)).toEqual({ pair: 1, pairs: 1 });
    expect(pairInStage(rounds, r(3, null))).toBeNull();
  });
});
