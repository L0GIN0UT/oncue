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

/** Замки одного браузера: эксклюзивный, очередь по порядку запросов, abort снимает ожидание. */
function fakeLocks() {
  const queue: Array<{ cb: () => Promise<void>; signal: AbortSignal; done: () => void }> = [];
  let held = false;
  const pump = () => {
    if (held) return;
    const next = queue.shift();
    if (!next) return;
    if (next.signal.aborted) return pump();
    held = true;
    void next.cb().then(() => {
      held = false;
      next.done();
      pump();
    });
  };
  return {
    request(_name: string, options: { signal: AbortSignal }, cb: () => Promise<void>) {
      return new Promise<void>((resolve, reject) => {
        options.signal.addEventListener("abort", () => reject(new Error("abort")));
        queue.push({ cb, signal: options.signal, done: resolve });
        pump();
      });
    },
  };
}

describe("startPresence: одна вкладка на браузер", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("две видимые вкладки — сигналы только от держателя замка", async () => {
    const locks = fakeLocks();
    const a = vi.fn(() => Promise.resolve());
    const b = vi.fn(() => Promise.resolve());
    const stopA = startPresence(a, 30_000, fakeDoc().doc, locks);
    const stopB = startPresence(b, 30_000, fakeDoc().doc, locks);
    await vi.advanceTimersByTimeAsync(90_000);
    expect(a).toHaveBeenCalledTimes(4);
    expect(b).not.toHaveBeenCalled();
    stopA();
    stopB();
  });

  it("когда держатель скрыт, замок переходит видимой вкладке", async () => {
    const locks = fakeLocks();
    const a = vi.fn(() => Promise.resolve());
    const b = vi.fn(() => Promise.resolve());
    const pageA = fakeDoc();
    const stopA = startPresence(a, 30_000, pageA.doc, locks);
    const stopB = startPresence(b, 30_000, fakeDoc().doc, locks);
    await vi.advanceTimersByTimeAsync(0);
    pageA.set("hidden");
    await vi.advanceTimersByTimeAsync(30_000);
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(2);
    stopA();
    stopB();
  });

  it("закрытие вкладки в очереди не оставляет сигналов", async () => {
    const locks = fakeLocks();
    const a = vi.fn(() => Promise.resolve());
    const b = vi.fn(() => Promise.resolve());
    const stopA = startPresence(a, 30_000, fakeDoc().doc, locks);
    const stopB = startPresence(b, 30_000, fakeDoc().doc, locks);
    stopB();
    stopA();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(b).not.toHaveBeenCalled();
    expect(a).toHaveBeenCalledTimes(1);
  });

  it("stop сразу после выдачи замка: таймера и удержания нет", async () => {
    // Замок выдаётся асинхронно: коллбэк выполняется уже после stop().
    let grant: (() => Promise<void>) | null = null;
    let released = false;
    const locks = {
      request(_n: string, _o: { signal: AbortSignal }, cb: () => Promise<void>) {
        grant = () => cb().then(() => void (released = true));
        return new Promise<void>(() => undefined);
      },
    };
    const send = vi.fn(() => Promise.resolve());
    const stop = startPresence(send, 30_000, fakeDoc().doc, locks);
    stop();
    await grant!();
    await vi.advanceTimersByTimeAsync(90_000);
    expect(send).not.toHaveBeenCalled();
    expect(released).toBe(true);
  });
});
