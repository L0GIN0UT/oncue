import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useRecoverPersonalBlocks } from "./DailyCards";

let finish: () => void = () => undefined;
vi.mock("@/features/auth/session", () => ({
  recoverSession: () =>
    new Promise<void>((resolve) => {
      finish = resolve;
    }),
}));

afterEach(() => vi.unstubAllGlobals());

describe("useRecoverPersonalBlocks", () => {
  it("не трогает состояние, если экран закрылся до конца восстановления", async () => {
    const refresh = vi.fn();
    const { unmount } = renderHook(() => useRecoverPersonalBlocks(["unavailable"], refresh));
    unmount();
    // Как после сноса окружения тестов: React читает window при любом setState.
    vi.stubGlobal("window", undefined);
    const rejections: unknown[] = [];
    const onRejection = (reason: unknown) => rejections.push(reason);
    process.on("unhandledRejection", onRejection);
    finish();
    await new Promise((resolve) => setTimeout(resolve, 0));
    process.off("unhandledRejection", onRejection);
    expect(rejections).toEqual([]);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("после восстановления сообщает, что ожидание закончено", async () => {
    const { result } = renderHook(() => useRecoverPersonalBlocks(["unavailable"], vi.fn()));
    expect(result.current).toBe(false);
    await act(async () => {
      finish();
      await Promise.resolve();
    });
    expect(result.current).toBe(true);
  });
});
