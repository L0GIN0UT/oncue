import { describe, expect, it } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { server, setupMockBackend, world } from "@/test/msw";
import { TestProviders } from "@/test/render";
import { useCollectionCoverArtwork, useCollectionCovers } from "./cover-artwork";

setupMockBackend();

const PUBLIC = "00000000-0000-4000-a000-000000000001";
const OTHER = "00000000-0000-4000-a000-000000000002";
const HIDDEN = "00000000-0000-4000-a000-000000000004";

const inHour = () => new Date(Date.now() + 3_600_000).toISOString();

describe("useCollectionCovers: обложки списков из collections", () => {
  it("одна пачка на группу: дубли схлопнуты, обложки подставлены по collection_id", async () => {
    const { result } = renderHook(() => useCollectionCovers([[PUBLIC, OTHER, PUBLIC, HIDDEN]]), {
      wrapper: TestProviders,
    });
    await waitFor(() => expect(result.current[PUBLIC]).toBeTruthy());
    expect(world.ugc.coverCalls).toEqual([[PUBLIC, OTHER, HIDDEN]]);
    expect(result.current[PUBLIC]?.url).toMatch(/^\/media\/a\//);
    // Закрытый и скрытый неотличимы от «без картинки»: null, а не ошибка.
    expect(result.current[HIDDEN]).toBeNull();
  });

  it("больше 50 id режутся на пачки по 50", async () => {
    const ids = Array.from(
      { length: 51 },
      (_, i) => `00000000-0000-4000-b000-${String(i).padStart(12, "0")}`,
    );
    renderHook(() => useCollectionCovers([ids]), { wrapper: TestProviders });
    await waitFor(() => expect(world.ugc.coverCalls).toHaveLength(2));
    expect(world.ugc.coverCalls.map((c) => c.length).sort((a, b) => a - b)).toEqual([1, 50]);
  });

  it("ошибка ручки тихо оставляет пустой результат: без повторов и без исключения", async () => {
    world.ugc.scenario.coversFail = true;
    const { result } = renderHook(() => useCollectionCovers([[PUBLIC]]), {
      wrapper: TestProviders,
    });
    await waitFor(() => expect(world.ugc.coverCalls).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 50));
    expect(result.current).toEqual({});
    expect(world.ugc.coverCalls).toHaveLength(1);
  });

  it("новая страница ленты не перезапрашивает уже полученные", async () => {
    const { result, rerender } = renderHook(({ g }) => useCollectionCovers(g), {
      wrapper: TestProviders,
      initialProps: { g: [[PUBLIC]] },
    });
    await waitFor(() => expect(result.current[PUBLIC]).toBeTruthy());
    rerender({ g: [[PUBLIC], [OTHER]] });
    await waitFor(() => expect(result.current[OTHER]).toBeTruthy());
    expect(world.ugc.coverCalls).toEqual([[PUBLIC], [OTHER]]);
  });
});

describe("useCollectionCoverArtwork: перевыдача ссылки", () => {
  it("ссылка в порядке: показываем её, свежую не просим", () => {
    const initial = { url: "/media/a/x.svg", expires_at: inHour(), mime: "image/svg+xml" };
    const { result } = renderHook(() => useCollectionCoverArtwork(PUBLIC, initial), {
      wrapper: TestProviders,
    });
    expect(result.current.url).toBe("/media/a/x.svg");
    expect(world.ugc.coverCalls).toEqual([]);
  });

  it("нет картинки (null): ссылки нет, ничего не запрашивается", () => {
    const { result } = renderHook(() => useCollectionCoverArtwork(PUBLIC, null), {
      wrapper: TestProviders,
    });
    act(() => result.current.onError());
    expect(result.current.url).toBeNull();
    expect(world.ugc.coverCalls).toEqual([]);
  });

  it("ссылка истекла по часам клиента: один запрос свежей по одному id", async () => {
    const initial = {
      url: "/media/a/old.svg",
      expires_at: new Date(Date.now() - 1_000).toISOString(),
      mime: "image/svg+xml",
    };
    const { result } = renderHook(() => useCollectionCoverArtwork(PUBLIC, initial), {
      wrapper: TestProviders,
    });
    await waitFor(() => expect(result.current.url).not.toBe("/media/a/old.svg"));
    expect(result.current.url).toMatch(/^\/media\/a\//);
    expect(world.ugc.coverCalls).toEqual([[PUBLIC]]);
  });

  it("onError: одна свежая ссылка; вторая ошибка оставляет пресет без новой выдачи", async () => {
    const initial = { url: "/media/a/bad.svg", expires_at: inHour(), mime: "image/svg+xml" };
    const { result } = renderHook(() => useCollectionCoverArtwork(PUBLIC, initial), {
      wrapper: TestProviders,
    });
    act(() => result.current.onError());
    await waitFor(() => expect(result.current.url).not.toBe("/media/a/bad.svg"));
    expect(result.current.url).toMatch(/^\/media\/a\//);
    expect(world.ugc.coverCalls).toEqual([[PUBLIC]]);

    act(() => result.current.onError());
    expect(result.current.url).toBeNull();
    expect(world.ugc.coverCalls).toEqual([[PUBLIC]]);
  });

  it("перевыдача упала: пресет, интерфейс без ошибки", async () => {
    server.use(http.post("*/api/web/v1/collections/covers", () => HttpResponse.error()));
    const initial = { url: "/media/a/bad.svg", expires_at: inHour(), mime: "image/svg+xml" };
    const { result } = renderHook(() => useCollectionCoverArtwork(PUBLIC, initial), {
      wrapper: TestProviders,
    });
    act(() => result.current.onError());
    await waitFor(() => expect(result.current.url).toBeNull());
  });
});
