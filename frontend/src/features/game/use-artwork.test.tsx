import { describe, expect, it } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { signInAsGuest, setupMockBackend, world } from "@/test/msw";
import { TestProviders } from "@/test/render";
import { useRoundArtwork } from "./use-artwork";

setupMockBackend();

function finishedSession() {
  const s = world.startSession(signInAsGuest(), world.mode("music.daily.classic")!, null);
  world.giveUp(
    s,
    s.rounds.find((x) => x.index === s.current)!,
  );
  return s;
}

describe("useRoundArtwork", () => {
  it("ссылка в порядке: показываем её, свежую не просим", () => {
    const s = finishedSession();
    const initial = {
      url: "/media/a/x.svg",
      expires_at: new Date(Date.now() + 60_000).toISOString(),
    };
    const { result } = renderHook(() => useRoundArtwork(s.id, 0, initial), {
      wrapper: TestProviders,
    });
    expect(result.current.url).toBe("/media/a/x.svg");
    expect(world.artworkCalls).toBe(0);
  });

  it("ссылка истекла по часам клиента: сразу одна свежая, без ошибки картинки", async () => {
    const s = finishedSession();
    const initial = {
      url: "/media/a/old.svg",
      expires_at: new Date(Date.now() - 1_000).toISOString(),
    };
    const { result } = renderHook(() => useRoundArtwork(s.id, 0, initial), {
      wrapper: TestProviders,
    });
    await waitFor(() => expect(result.current.url).not.toBe("/media/a/old.svg"));
    expect(result.current.url).toMatch(/^\/media\/a\//);
    expect(world.artworkCalls).toBe(1);
  });

  it("нет artwork (null): ссылки нет и ничего не запрашивается", () => {
    const s = finishedSession();
    const { result } = renderHook(() => useRoundArtwork(s.id, 0, null), { wrapper: TestProviders });
    expect(result.current.url).toBeNull();
    expect(world.artworkCalls).toBe(0);
  });
});
