import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  hasUnavailableBlock,
  keepLastKnownBlocks,
  RETRY_UNAVAILABLE_MS,
  useRetryUnavailableOnce,
} from "./page-blocks";

const ok = <T,>(data: T) => ({ status: "ok" as const, data });
const down = { status: "unavailable" as const, data: null };

type Page = { blocks: Record<string, { status: "ok" | "unavailable"; data?: unknown } | null> };

describe("keepLastKnownBlocks", () => {
  it("погасший общий блок остаётся прежним", () => {
    const prev: Page = { blocks: { modes: ok(["a"]), records: ok([1]) } };
    const next: Page = { blocks: { modes: down, records: ok([2]) } };
    const merged = keepLastKnownBlocks(prev, next, ["modes"]);
    expect(merged.blocks.modes).toEqual(ok(["a"]));
    expect(merged.blocks.records).toEqual(ok([2]));
  });

  it("личные блоки (не из списка) прежними не подставляются", () => {
    const prev: Page = { blocks: { records: ok([1]) } };
    const next: Page = { blocks: { records: down } };
    expect(keepLastKnownBlocks(prev, next, ["modes"]).blocks.records).toEqual(down);
  });

  it("прежнего ответа нет или он тоже был недоступен — как пришло", () => {
    const next: Page = { blocks: { modes: down } };
    expect(keepLastKnownBlocks(undefined, next, ["modes"])).toBe(next);
    expect(keepLastKnownBlocks({ blocks: { modes: down } }, next, ["modes"])).toBe(next);
  });

  it("блок исправен — ответ возвращается без копии", () => {
    const next: Page = { blocks: { modes: ok(["b"]) } };
    expect(keepLastKnownBlocks({ blocks: { modes: ok(["a"]) } }, next, ["modes"])).toBe(next);
  });
});

describe("hasUnavailableBlock", () => {
  it("видит погасший блок; блок вне списка и отсутствующий блок не считаются", () => {
    expect(hasUnavailableBlock({ blocks: { modes: down } })).toBe(true);
    expect(hasUnavailableBlock({ blocks: { modes: down } }, ["random"])).toBe(false);
    expect(hasUnavailableBlock({ blocks: { modes: null } })).toBe(false);
    expect(hasUnavailableBlock(undefined)).toBe(false);
  });
});

describe("useRetryUnavailableOnce", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("погасший блок — ровно один повтор через паузу, второго нет", () => {
    const refetch = vi.fn();
    const page: Page = { blocks: { modes: down } };
    const { rerender } = renderHook(() => useRetryUnavailableOnce(page, refetch));
    expect(refetch).not.toHaveBeenCalled();
    act(() => void vi.advanceTimersByTime(RETRY_UNAVAILABLE_MS));
    expect(refetch).toHaveBeenCalledTimes(1);
    // Повтор вернул тот же сбой (новый объект страницы): повтора больше нет.
    rerender();
    act(() => void vi.advanceTimersByTime(RETRY_UNAVAILABLE_MS * 3));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("блоки целы — повторов нет; после выздоровления следующий сбой снова получает повтор", () => {
    const refetch = vi.fn();
    let page: Page = { blocks: { modes: ok([]) } };
    const { rerender } = renderHook(() => useRetryUnavailableOnce(page, refetch));
    act(() => void vi.advanceTimersByTime(RETRY_UNAVAILABLE_MS * 2));
    expect(refetch).not.toHaveBeenCalled();

    page = { blocks: { modes: down } };
    rerender();
    act(() => void vi.advanceTimersByTime(RETRY_UNAVAILABLE_MS));
    expect(refetch).toHaveBeenCalledTimes(1);

    page = { blocks: { modes: ok([]) } };
    rerender();
    page = { blocks: { modes: down } };
    rerender();
    act(() => void vi.advanceTimersByTime(RETRY_UNAVAILABLE_MS));
    expect(refetch).toHaveBeenCalledTimes(2);
  });
});
