import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { signInAsGuest, setupMockBackend, world } from "@/test/msw";
import { TestProviders } from "@/test/render";
import { msUntilExpiry, useFinalClue } from "./use-final-clue";

setupMockBackend();

/** Сессия с завершённым первым раундом (ревил есть), ссылка ревила — из снимка. */
function finishedRound(key = "film.random.frame") {
  const s = world.startSession(signInAsGuest(), world.mode(key)!, null);
  world.giveUp(s, s.rounds[0]!);
  const clue = world.snapshot(s).rounds[0]!.reveal!.final_clue;
  return { s, clue };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("useFinalClue", () => {
  it("ссылка жива — ассет в Blob без лишних final-clue", async () => {
    const { s, clue } = finishedRound();
    const { result } = renderHook(() => useFinalClue(s.id, 0, clue), { wrapper: TestProviders });
    await waitFor(() => expect(result.current.objectUrl).toMatch(/^blob:/));
    expect(result.current.kind).toBe("image");
    expect(world.finalClueCalls).toBe(0);
  });

  it("ошибка загрузки (403 по старой ссылке) — один final-clue и кадр со второй ссылки", async () => {
    world.scenario.staleRevealLinks = true;
    const { s, clue } = finishedRound();
    const { result } = renderHook(() => useFinalClue(s.id, 0, clue), { wrapper: TestProviders });
    await waitFor(() => expect(result.current.objectUrl).toMatch(/^blob:/));
    expect(world.finalClueCalls).toBe(1);
  });

  it("ссылка уже истекла по часам клиента — сразу свежая, старую не качаем", async () => {
    const { s, clue } = finishedRound();
    const expired = { ...clue, expires_at: new Date(Date.now() - 1000).toISOString() };
    const { result } = renderHook(() => useFinalClue(s.id, 0, expired), {
      wrapper: TestProviders,
    });
    await waitFor(() => expect(result.current.objectUrl).toMatch(/^blob:/));
    expect(world.finalClueCalls).toBe(1);
  });

  it("ревил открыт дольше TTL — по истечении ссылки берёт свежую один раз", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["setTimeout", "clearTimeout", "Date"] });
    const { s, clue } = finishedRound();
    const { result } = renderHook(() => useFinalClue(s.id, 0, clue), { wrapper: TestProviders });
    await waitFor(() => expect(result.current.objectUrl).toMatch(/^blob:/));
    const first = result.current.objectUrl;
    expect(world.finalClueCalls).toBe(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(61_000);
    });
    await waitFor(() => expect(world.finalClueCalls).toBe(1));
    await waitFor(() => expect(result.current.objectUrl).not.toBe(first));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(world.finalClueCalls).toBe(1);
  });

  it("срок берётся из expires_at, а не из константы: TTL 300 с — до конца срока без final-clue", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["setTimeout", "clearTimeout", "Date"] });
    const { s, clue } = finishedRound();
    const longLived = { ...clue, expires_at: new Date(Date.now() + 300_000).toISOString() };
    const { result } = renderHook(() => useFinalClue(s.id, 0, longLived), {
      wrapper: TestProviders,
    });
    await waitFor(() => expect(result.current.objectUrl).toMatch(/^blob:/));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200_000); // давно за 60 с, но ссылка ещё жива
    });
    expect(world.finalClueCalls).toBe(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(101_000);
    });
    await waitFor(() => expect(world.finalClueCalls).toBe(1));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600_000); // дальше не опрашивает
    });
    expect(world.finalClueCalls).toBe(1);
  });

  it("final-clue отказал (раунд не засчитан) и Blob нет — failed", async () => {
    world.scenario.staleRevealLinks = true;
    const { s, clue } = finishedRound();
    s.rounds[0]!.status = "void";
    const { result } = renderHook(() => useFinalClue(s.id, 0, clue), { wrapper: TestProviders });
    await waitFor(() => expect(result.current.failed).toBe(true));
    expect(result.current.objectUrl).toBeNull();
  });
});

describe("msUntilExpiry", () => {
  it("остаток по часам клиента; неразборчивая дата — истекла", () => {
    const now = Date.parse("2026-10-16T10:00:00Z");
    expect(msUntilExpiry("2026-10-16T10:01:00Z", now)).toBe(60_000);
    expect(msUntilExpiry("2026-10-16T09:59:00Z", now)).toBeLessThan(0);
    expect(msUntilExpiry("nope", now)).toBe(0);
  });
});
