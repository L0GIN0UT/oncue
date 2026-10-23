import { describe, expect, it } from "vitest";
import { chancePercent, dayRelation, isMaxLevel, spinOutcome, xpPercent } from "./logic";

describe("xpPercent", () => {
  it("доля набранного внутри уровня", () => {
    expect(xpPercent(120, 380)).toBe(24);
    expect(xpPercent(250, 250)).toBe(50);
  });
  it("граница уровня: 0 опыта — пустая полоса", () => {
    expect(xpPercent(0, 500)).toBe(0);
  });
  it("почти следующий уровень не округляется до 100", () => {
    expect(xpPercent(999, 1)).toBe(99);
  });
  it("высший уровень (xp_to_next = null) — полная полоса", () => {
    expect(xpPercent(1234, null)).toBe(100);
  });
});

describe("isMaxLevel", () => {
  it("высший — когда до следующего опыта нет", () => {
    expect(isMaxLevel({ xp_to_next: null })).toBe(true);
    expect(isMaxLevel({ xp_to_next: 1 })).toBe(false);
  });
});

describe("chancePercent", () => {
  it("шанс сектора в процентах без хвоста", () => {
    expect(chancePercent(0.3)).toBe(30);
    expect(chancePercent(0.0833)).toBe(8.3);
  });
});

describe("dayRelation", () => {
  const now = new Date(2026, 9, 23, 15, 0);
  it("сегодня, завтра и позже — по календарным суткам, а не по 24 часам", () => {
    expect(dayRelation(new Date(2026, 9, 23, 23, 59), now)).toBe("today");
    expect(dayRelation(new Date(2026, 9, 24, 0, 0), now)).toBe("tomorrow");
    expect(dayRelation(new Date(2026, 9, 24, 23, 0), now)).toBe("tomorrow");
    expect(dayRelation(new Date(2026, 9, 25, 0, 0), now)).toBe("later");
  });
  it("граница месяца", () => {
    expect(dayRelation(new Date(2026, 10, 1, 0, 0), new Date(2026, 9, 31, 12, 0))).toBe("tomorrow");
  });
});

describe("spinOutcome", () => {
  it("первый спин и повтор различаются", () => {
    expect(spinOutcome({ repeated: false })).toBe("new");
    expect(spinOutcome({ repeated: true })).toBe("repeated");
  });
});
