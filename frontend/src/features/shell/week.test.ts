import { describe, expect, it } from "vitest";
import { weekCells } from "./week";

const states = (streak: number, played: boolean) =>
  weekCells(new Date(2026, 9, 26), streak, played).map((c) => c.state);

describe("weekCells", () => {
  it("семь клеток, заканчивая сегодняшним числом", () => {
    const cells = weekCells(new Date(2026, 9, 26), 0, false);
    expect(cells.map((c) => c.day)).toEqual([20, 21, 22, 23, 24, 25, 26]);
  });

  it("считает дни через границу месяца", () => {
    const cells = weekCells(new Date(2026, 10, 2), 0, false);
    expect(cells.map((c) => c.day)).toEqual([27, 28, 29, 30, 31, 1, 2]);
  });

  it("серия 3, сегодня ещё не сыграно: вчера и два дня до него, сегодня — пунктир", () => {
    expect(states(3, false)).toEqual([
      "missed",
      "missed",
      "missed",
      "played",
      "played",
      "played",
      "today",
    ]);
  });

  it("серия 3, сегодня сыграно: сегодня входит в серию", () => {
    expect(states(3, true)).toEqual([
      "missed",
      "missed",
      "missed",
      "missed",
      "played",
      "played",
      "played",
    ]);
  });

  it("серия длиннее недели закрашивает всю неделю", () => {
    expect(states(12, true).every((s) => s === "played")).toBe(true);
  });

  it("без серии закрашен только сыгранный сегодня", () => {
    expect(states(0, false).filter((s) => s === "played")).toHaveLength(0);
    expect(states(1, true).filter((s) => s === "played")).toHaveLength(1);
  });
});
