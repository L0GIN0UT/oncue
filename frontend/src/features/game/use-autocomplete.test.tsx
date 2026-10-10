import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { delay, http, HttpResponse } from "msw";
import { server, setupMockBackend } from "@/test/msw";
import { MAX_QUERY, normalizeQuery, useAutocomplete } from "./use-autocomplete";

setupMockBackend();

type Seen = { q: string | null; domain: string | null };

function trackRequests(): Seen[] {
  const seen: Seen[] = [];
  server.events.on("request:start", ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname.endsWith("/autocomplete"))
      seen.push({ q: url.searchParams.get("q"), domain: url.searchParams.get("domain") });
  });
  return seen;
}

describe("useAutocomplete", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    server.events.removeAllListeners();
  });

  it("ждёт паузу 200 мс и шлёт один запрос на последний ввод", async () => {
    const seen = trackRequests();
    const { result, rerender } = renderHook(({ q }) => useAutocomplete(q, "music"), {
      initialProps: { q: "з" },
    });
    rerender({ q: "зв" });
    rerender({ q: "зве" });
    await act(() => vi.advanceTimersByTimeAsync(150));
    expect(seen).toHaveLength(0);
    await act(() => vi.advanceTimersByTimeAsync(100));
    await vi.waitFor(() => expect(result.current.items.length).toBeGreaterThan(0));
    expect(seen).toEqual([expect.objectContaining({ q: "зве", domain: "music" })]);
    expect(result.current.items.every((i) => i.domain === "music")).toBe(true);
  });

  it("отменяет устаревший запрос, когда ввод изменился", async () => {
    const seen = trackRequests();
    const abort = vi.spyOn(AbortController.prototype, "abort");
    // Первый запрос «висит» дольше, чем пользователь вводит следующий символ.
    server.use(
      http.get("*/api/catalog/v1/autocomplete", async ({ request }) => {
        if (new URL(request.url).searchParams.get("q") !== "брат") return undefined;
        await delay(1000);
        return HttpResponse.json({
          items: [{ id: "x", domain: "film", title: "Брат", year: 1997, subtitle: null }],
        });
      }),
    );
    const { result, rerender } = renderHook(({ q }) => useAutocomplete(q, "film"), {
      initialProps: { q: "брат" },
    });
    await act(() => vi.advanceTimersByTimeAsync(210));
    rerender({ q: "сол" });
    await act(() => vi.advanceTimersByTimeAsync(210));
    await vi.waitFor(() => expect(result.current.items.map((i) => i.title)).toEqual(["Солярис"]));
    expect(seen.map((s) => s.q)).toEqual(["брат", "сол"]);
    expect(abort).toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(1200));
    expect(result.current.items.map((i) => i.title)).toEqual(["Солярис"]);
  });

  it("пустой ввод не делает запросов и очищает подсказки", async () => {
    const seen = trackRequests();
    const { result } = renderHook(() => useAutocomplete("   ", "music"));
    await act(() => vi.advanceTimersByTimeAsync(300));
    expect(seen).toHaveLength(0);
    expect(result.current.items).toEqual([]);
  });

  it("обрезает запрос до 64 символов", () => {
    expect(normalizeQuery(`  ${"а".repeat(100)} `)).toHaveLength(MAX_QUERY);
  });
});
