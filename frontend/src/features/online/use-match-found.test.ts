import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { FOUND_DELAY_MS, useMatchFound } from "./use-match-found";

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("useMatchFound", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    document.title = "Онлайн · OnCue";
  });
  afterEach(() => {
    setHidden(false);
    vi.useRealTimers();
  });

  it("без кода комнаты ничего не делает", () => {
    const go = vi.fn();
    renderHook(() => useMatchFound(null, "Соперник найден!", go));
    vi.advanceTimersByTime(5000);
    expect(go).not.toHaveBeenCalled();
  });

  it("видимая вкладка: переход после короткой паузы, заголовок не трогаем", () => {
    const go = vi.fn();
    renderHook(() => useMatchFound("K7MQ2XWP", "Соперник найден!", go));
    expect(go).not.toHaveBeenCalled();
    vi.advanceTimersByTime(FOUND_DELAY_MS);
    expect(go).toHaveBeenCalledWith("K7MQ2XWP");
    expect(document.title).toBe("Онлайн · OnCue");
  });

  it("фоновая вкладка: заголовок мигает, переход — только при возвращении", () => {
    setHidden(true);
    const go = vi.fn();
    renderHook(() => useMatchFound("K7MQ2XWP", "Соперник найден!", go));
    expect(document.title).toBe("Соперник найден!");
    vi.advanceTimersByTime(1000);
    expect(document.title).toBe("Онлайн · OnCue");
    vi.advanceTimersByTime(60_000);
    expect(go).not.toHaveBeenCalled();
    setHidden(false);
    expect(go).toHaveBeenCalledWith("K7MQ2XWP");
    expect(document.title).toBe("Онлайн · OnCue");
  });

  it("размонтирование отменяет переход и мигание", () => {
    const go = vi.fn();
    const visible = renderHook(() => useMatchFound("AAAA2222", "x", go));
    visible.unmount();
    vi.advanceTimersByTime(FOUND_DELAY_MS * 2);
    expect(go).not.toHaveBeenCalled();

    setHidden(true);
    const hidden = renderHook(() => useMatchFound("BBBB3333", "Найден", go));
    hidden.unmount();
    expect(document.title).toBe("Онлайн · OnCue");
    setHidden(false);
    expect(go).not.toHaveBeenCalled();
  });
});
