import { describe, expect, it } from "vitest";
import { dayIn, formatClock, nextMidnight, remainingUntil } from "./countdown-time";

describe("отсчёт до смены дня (Europe/Moscow)", () => {
  it("полночь по Москве — 21:00 UTC предыдущего дня", () => {
    expect(nextMidnight(new Date("2026-10-10T12:00:00Z")).toISOString()).toBe(
      "2026-10-10T21:00:00.000Z",
    );
    // 22:30 UTC — уже 01:30 по Москве следующего дня.
    expect(nextMidnight(new Date("2026-10-10T22:30:00Z")).toISOString()).toBe(
      "2026-10-11T21:00:00.000Z",
    );
  });

  it("игровой день — дата по Москве", () => {
    expect(dayIn(new Date("2026-10-10T20:59:59Z"))).toBe("2026-10-10");
    expect(dayIn(new Date("2026-10-10T21:00:00Z"))).toBe("2026-10-11");
  });

  it("учитывает переход на летнее время в другом поясе", () => {
    // Берлин: 2026-03-29 переход на UTC+2, полночь 30 марта — 22:00 UTC.
    expect(nextMidnight(new Date("2026-03-29T12:00:00Z"), "Europe/Berlin").toISOString()).toBe(
      "2026-03-29T22:00:00.000Z",
    );
  });

  it("формат ЧЧ:ММ:СС и нули после наступления", () => {
    const target = new Date("2026-10-10T21:00:00Z");
    expect(formatClock(remainingUntil(target, new Date("2026-10-10T13:17:05Z")))).toBe("07:42:55");
    expect(remainingUntil(target, new Date("2026-10-11T00:00:00Z"))).toMatchObject({
      totalMs: 0,
      hours: 0,
    });
  });
});
