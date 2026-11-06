import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import { setupMockBackend } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import type { LevelClue } from "@/shared/api/types";
import type { RoundRevealDelta } from "@/shared/realtime";
import { RoundRevealPanel } from "./RoundRevealPanel";
import { loadLevelClue, matchPreloadKey, preloadLevelClue } from "./use-level-clue";

setupMockBackend();

const clue = (url: string, level = 1): LevelClue => ({
  level,
  kind: "audio",
  url,
  expires_at: "2099-01-01T00:00:00Z",
  mime: "audio/mpeg",
  meta: {},
});

const okResponse = () => new Response(new Blob(["x"], { type: "audio/mpeg" }), { status: 200 });

const reveal = (extra: Partial<RoundRevealDelta> = {}) =>
  ({
    round_index: 0,
    media_item_id: "m1",
    title: "Nevermind",
    year: 1991,
    persons: [],
    final_clue: null,
    results: [],
    ...extra,
  }) as RoundRevealDelta;

afterEach(() => vi.restoreAllMocks());

describe("предзагрузка уровня 1 следующего раунда в комнате (ADR 0080)", () => {
  it("файл кладётся в кэш под следующий раунд", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => okResponse());
    const client = new QueryClient();
    await preloadLevelClue(client, "K7MQ2XWP", 1, clue("/media/a/next.mp3"));
    expect(client.getQueryData(matchPreloadKey("K7MQ2XWP", 1))).toBeInstanceOf(Blob);
    // повтор не качает второй раз
    await preloadLevelClue(client, "K7MQ2XWP", 1, clue("/media/a/next.mp3"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("сбой скачивания молча игнорируется, кэш пуст", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("network"));
    const client = new QueryClient();
    await expect(
      preloadLevelClue(client, "K7MQ2XWP", 1, clue("/media/a/next.mp3")),
    ).resolves.toBeUndefined();
    expect(client.getQueryData(matchPreloadKey("K7MQ2XWP", 1))).toBeUndefined();
  });

  it("не уровень 1 не предзагружается", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => okResponse());
    const client = new QueryClient();
    await preloadLevelClue(client, "K7MQ2XWP", 1, clue("/media/a/next.mp3", 2));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("старт раунда берёт уже скачанный blob и не качает ссылку уровня", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => okResponse());
    const blob = new Blob(["pre"]);
    const loaded = await loadLevelClue(clue("/media/a/level.mp3"), blob);
    expect(loaded.blob).toBe(blob);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("без предзагрузки уровень качается как раньше", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => okResponse());
    await loadLevelClue(clue("/media/a/level.mp3"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("панель ревила запускает предзагрузку по next_round_first_clue", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => okResponse());
    renderWithProviders(
      <RoundRevealPanel
        code="K7MQ2XWP"
        reveal={reveal({ next_round_first_clue: clue("/media/a/next.mp3") })}
        players={[]}
        domain="music"
      />,
    );
    expect(screen.getByTestId("reveal-title")).toHaveTextContent("Nevermind");
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/media/a/next.mp3"))).toBe(
        true,
      ),
    );
  });

  it("без поля панель ничего не качает", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => okResponse());
    renderWithProviders(
      <RoundRevealPanel code="K7MQ2XWP" reveal={reveal()} players={[]} domain="music" />,
    );
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/media/"))).toBe(false);
  });
});
