import { describe, expect, it, vi } from "vitest";
import { REFRESH_PATH, refreshSession, type RefreshDeps } from "./refresh";

/** Web Locks: обратные вызовы выполняются строго по очереди, как между вкладками. */
function fakeLocks() {
  let tail: Promise<unknown> = Promise.resolve();
  return {
    request<T>(_name: string, cb: () => Promise<T>): Promise<T> {
      const run = tail.then(cb, cb);
      tail = run.catch(() => undefined);
      return run;
    },
  };
}

describe("refreshSession", () => {
  it("одновременные 401 во вкладке — один refresh", async () => {
    let csrf = "old";
    const fetch = vi.fn(async () => {
      csrf = "new";
      return new Response(null, { status: 200 });
    });
    const deps: RefreshDeps = {
      fetch: fetch as unknown as typeof globalThis.fetch,
      locks: fakeLocks(),
      readCsrf: () => csrf,
    };
    const results = await Promise.all([
      refreshSession("old", deps),
      refreshSession("old", deps),
      refreshSession("old", deps),
    ]);
    expect(results).toEqual([true, true, true]);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith(
      REFRESH_PATH,
      expect.objectContaining({ method: "POST", headers: { "X-CSRF-Token": "old" } }),
    );
  });

  it("если соседняя вкладка уже обновила сессию (gv_csrf сменился), refresh не повторяется", async () => {
    const fetch = vi.fn();
    const ok = await refreshSession("old", {
      fetch,
      locks: fakeLocks(),
      readCsrf: () => "rotated-by-other-tab",
    });
    expect(ok).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("вторая вкладка ждёт блокировку и видит результат первой", async () => {
    let csrf = "old";
    const locks = fakeLocks();
    const fetch = vi.fn(async () => {
      await new Promise((r) => setTimeout(r, 10));
      csrf = "new";
      return new Response(null, { status: 200 });
    });
    const tabA: RefreshDeps = {
      fetch: fetch as unknown as typeof globalThis.fetch,
      locks,
      readCsrf: () => csrf,
    };
    const first = refreshSession("old", tabA);
    // Вторая «вкладка» — отдельный модульный single-flight не разделяет, поэтому эмулируем её
    // прямым вызовом обратного вызова блокировки после первой.
    const second = locks.request("gv-auth-refresh", async () => csrf !== "old");
    expect(await first).toBe(true);
    expect(await second).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("отказ refresh — false", async () => {
    const fetch = vi.fn(async () => new Response(null, { status: 401 }));
    expect(
      await refreshSession("a", {
        fetch: fetch as unknown as typeof globalThis.fetch,
        locks: null,
        readCsrf: () => "a",
      }),
    ).toBe(false);
  });
});
