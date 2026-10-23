import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flashTitle } from "./title-flash";

describe("flashTitle", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    document.title = "Онлайн · OnCue";
  });
  afterEach(() => vi.useRealTimers());

  it("мигает между исходным заголовком и текстом сигнала, stop возвращает исходный", () => {
    const stop = flashTitle("Соперник найден!", 1000);
    expect(document.title).toBe("Соперник найден!");
    vi.advanceTimersByTime(1000);
    expect(document.title).toBe("Онлайн · OnCue");
    vi.advanceTimersByTime(1000);
    expect(document.title).toBe("Соперник найден!");
    stop();
    expect(document.title).toBe("Онлайн · OnCue");
    vi.advanceTimersByTime(5000);
    expect(document.title).toBe("Онлайн · OnCue");
  });

  it("prefers-reduced-motion: заголовок не мигает, stop возвращает исходный", () => {
    const original = globalThis.matchMedia;
    globalThis.matchMedia = ((q: string) => ({
      matches: q.includes("reduce"),
    })) as typeof matchMedia;
    try {
      const stop = flashTitle("Соперник найден!", 1000);
      expect(document.title).toBe("Соперник найден!");
      vi.advanceTimersByTime(3000);
      expect(document.title).toBe("Соперник найден!");
      stop();
      expect(document.title).toBe("Онлайн · OnCue");
    } finally {
      globalThis.matchMedia = original;
    }
  });

  it("повторный stop безопасен", () => {
    const stop = flashTitle("x", 500);
    stop();
    stop();
    expect(document.title).toBe("Онлайн · OnCue");
  });
});
