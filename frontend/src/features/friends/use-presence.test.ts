import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startPresence } from "./use-presence";

function fakeDoc(initial: DocumentVisibilityState = "visible") {
  const listeners = new Set<() => void>();
  const doc = {
    visibilityState: initial,
    addEventListener: (_: string, fn: () => void): void => void listeners.add(fn),
    removeEventListener: (_: string, fn: () => void): void => void listeners.delete(fn),
  };
  return {
    doc,
    listeners,
    set(state: DocumentVisibilityState) {
      doc.visibilityState = state;
      for (const fn of [...listeners]) fn();
    },
  };
}

describe("startPresence", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("шлёт сигнал сразу и затем по интервалу, пока вкладка видима", () => {
    const send = vi.fn(() => Promise.resolve());
    const { doc } = fakeDoc();
    const stop = startPresence(send, 30_000, doc);
    expect(send).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(30_000);
    expect(send).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(60_000);
    expect(send).toHaveBeenCalledTimes(4);
    stop();
  });

  it("скрытая вкладка не шлёт; при возврате — сигнал сразу", () => {
    const send = vi.fn(() => Promise.resolve());
    const page = fakeDoc();
    const stop = startPresence(send, 30_000, page.doc);
    page.set("hidden");
    vi.advanceTimersByTime(120_000);
    expect(send).toHaveBeenCalledTimes(1);
    page.set("visible");
    expect(send).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(30_000);
    expect(send).toHaveBeenCalledTimes(3);
    stop();
  });

  it("не стартует в скрытой вкладке; остановка снимает таймер и слушатель", () => {
    const send = vi.fn(() => Promise.resolve());
    const page = fakeDoc("hidden");
    const stop = startPresence(send, 30_000, page.doc);
    expect(send).not.toHaveBeenCalled();
    page.set("visible");
    expect(send).toHaveBeenCalledTimes(1);
    stop();
    vi.advanceTimersByTime(90_000);
    expect(send).toHaveBeenCalledTimes(1);
    expect(page.listeners.size).toBe(0);
  });

  it("сбой отправки не ломает цикл", async () => {
    const send = vi.fn(() => Promise.reject(new Error("net")));
    const stop = startPresence(send, 1000, fakeDoc().doc);
    await vi.advanceTimersByTimeAsync(3000);
    expect(send).toHaveBeenCalledTimes(4);
    stop();
  });
});
