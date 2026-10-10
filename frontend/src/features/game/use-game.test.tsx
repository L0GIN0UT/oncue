import { describe, expect, it } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { signInAsGuest, setupMockBackend, world } from "@/test/msw";
import { TestProviders } from "@/test/render";
import { ApiError, NetworkError } from "@/shared/api/errors";
import { MODES } from "../../../e2e/mocks/world";
import { classifyError, useGame } from "./use-game";

setupMockBackend();

describe("useGame", () => {
  it("409 game.desync применяет снимок из details без лишнего запроса", async () => {
    const subject = signInAsGuest();
    const s = world.startSession(subject, MODES[0]!, null);
    const { result } = renderHook(() => useGame(s.id), { wrapper: TestProviders });
    await waitFor(() => expect(result.current.state.snapshot?.version).toBe(0));
    world.scenario.desyncOnce = true;
    await act(() => result.current.act({ kind: "skip" }));
    expect(result.current.state.snapshot?.version).toBe(1);
    expect(result.current.state.busy).toBe(false);
    // Повтор хода после ресинка проходит с актуальной версией.
    await act(() => result.current.act({ kind: "skip" }));
    expect(result.current.state.snapshot?.rounds[0]?.level).toBe(2);
  });

  it("верный ответ открывает ревил с названием только после завершения раунда", async () => {
    const subject = signInAsGuest();
    const s = world.startSession(subject, MODES[0]!, null);
    const { result } = renderHook(() => useGame(s.id), { wrapper: TestProviders });
    await waitFor(() => expect(result.current.state.snapshot).not.toBeNull());
    expect(JSON.stringify(result.current.state.snapshot)).not.toContain(s.rounds[0]!.itemId);
    await act(() =>
      result.current.act({ kind: "guess", answer: { media_item_id: s.rounds[0]!.itemId } }),
    );
    expect(result.current.state.revealRound).toBe(0);
    expect(result.current.state.snapshot?.rounds[0]?.reveal?.title).toBeTruthy();
    expect(result.current.state.snapshot?.score).toBe(6);
  });
});

describe("classifyError", () => {
  it("сопоставляет ошибки плана §7 с реакцией экрана", () => {
    expect(classifyError(new NetworkError())).toEqual({ notice: "network", resync: true });
    expect(classifyError(new ApiError(410, { code: "game.asset_unavailable" }))).toEqual({
      notice: "round_void",
      resync: true,
    });
    expect(classifyError(new ApiError(429, { code: "common.rate_limited" }))).toEqual({
      notice: "rate_limited",
      resync: false,
    });
    expect(classifyError(new ApiError(409, { code: "game.round_finished" }))).toEqual({
      notice: null,
      resync: true,
    });
    expect(classifyError(new ApiError(409, { code: "game.desync", details: {} }))).toEqual({
      notice: "desync",
      resync: true,
    });
  });
});
