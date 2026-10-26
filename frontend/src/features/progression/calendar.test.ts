import { describe, expect, it } from "vitest";
import { monthGrid } from "./calendar";

const oct26 = new Date(2026, 9, 26);

describe("monthGrid", () => {
  it("октябрь 2026 начинается со четверга: три пустые клетки, 31 день", () => {
    const grid = monthGrid(oct26, 0, false);
    expect(grid.leading).toBe(3);
    expect(grid.days).toHaveLength(31);
  });

  it("серия до вчера: сегодня ждёт игры, дни серии заполнены, дальше пропущено, будущее пустое", () => {
    const { days } = monthGrid(oct26, 3, false);
    const state = (d: number) => days[d - 1]!.state;
    expect(state(26)).toBe("today");
    expect([25, 24, 23].map(state)).toEqual(["played", "played", "played"]);
    expect(state(22)).toBe("missed");
    expect(state(27)).toBe("future");
  });

  it("сыграно сегодня: сегодняшний день входит в серию", () => {
    const { days } = monthGrid(oct26, 3, true);
    expect([26, 25, 24].map((d) => days[d - 1]!.state)).toEqual(["played", "played", "played"]);
    expect(days[22]!.state).toBe("missed");
  });

  it("без серии все прошедшие дни пропущены", () => {
    const { days } = monthGrid(oct26, 0, false);
    expect(days.filter((d) => d.state === "played")).toHaveLength(0);
    expect(days[0]!.state).toBe("missed");
  });
});
