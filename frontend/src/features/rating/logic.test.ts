import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/shared/api/errors";
import { classifyBoardError, daysLeft, pointsToNext, pollMatchChanges, signedDelta } from "./logic";

describe("daysLeft", () => {
  const end = "2026-11-01T00:00:00Z";
  it("округляет вверх: неполный день считается днём", () => {
    expect(daysLeft(end, Date.parse("2026-10-30T12:00:00Z"))).toBe(2);
    expect(daysLeft(end, Date.parse("2026-10-24T00:00:00Z"))).toBe(8);
  });
  it("после окончания — 0, не отрицательное", () => {
    expect(daysLeft(end, Date.parse("2026-11-05T00:00:00Z"))).toBe(0);
  });
  it("битая дата — 0", () => {
    expect(daysLeft("не дата", 0)).toBe(0);
  });
});

describe("pointsToNext", () => {
  it("разница до порога следующего звания", () => {
    expect(pointsToNext(1180, 1200)).toBe(20);
  });
  it("нет порога (высшее звание) или нет рейтинга (калибровка) — null", () => {
    expect(pointsToNext(2500, null)).toBeNull();
    expect(pointsToNext(null, 700)).toBeNull();
    expect(pointsToNext(1000, undefined)).toBeNull();
  });
  it("не уходит в минус", () => {
    expect(pointsToNext(1300, 1200)).toBe(0);
  });
});

describe("signedDelta", () => {
  it("знак всегда виден, минус типографский", () => {
    expect(signedDelta(18)).toBe("+18");
    expect(signedDelta(-7)).toBe("−7");
    expect(signedDelta(0)).toBe("0");
  });
});

describe("pollMatchChanges", () => {
  const notRated = () => new ApiError(404, { code: "rating.match_not_rated" });
  const result = { match_id: "m", items: [] };

  it("повторяет при 404 match_not_rated, пока не получит ответ", async () => {
    const fetchOnce = vi
      .fn()
      .mockRejectedValueOnce(notRated())
      .mockRejectedValueOnce(notRated())
      .mockResolvedValueOnce(result);
    const wait = vi.fn().mockResolvedValue(undefined);
    await expect(pollMatchChanges(fetchOnce, { attempts: 10, delayMs: 1000, wait })).resolves.toBe(
      result,
    );
    expect(fetchOnce).toHaveBeenCalledTimes(3);
    expect(wait).toHaveBeenCalledTimes(2);
    expect(wait).toHaveBeenCalledWith(1000, undefined);
  });

  it("исчерпал попытки — null (итог не ломается)", async () => {
    const fetchOnce = vi.fn().mockRejectedValue(notRated());
    const wait = vi.fn().mockResolvedValue(undefined);
    await expect(
      pollMatchChanges(fetchOnce, { attempts: 4, delayMs: 10, wait }),
    ).resolves.toBeNull();
    expect(fetchOnce).toHaveBeenCalledTimes(4);
    // После последней попытки не ждём.
    expect(wait).toHaveBeenCalledTimes(3);
  });

  it("другая ошибка пробрасывается сразу, без повторов", async () => {
    const boom = new ApiError(403, { code: "auth.forbidden" });
    const fetchOnce = vi.fn().mockRejectedValue(boom);
    await expect(
      pollMatchChanges(fetchOnce, { attempts: 5, delayMs: 10, wait: vi.fn() }),
    ).rejects.toBe(boom);
    expect(fetchOnce).toHaveBeenCalledOnce();
  });
});

describe("pollMatchChanges — отмена", () => {
  it("abort прекращает опрос без новых запросов", async () => {
    const ctrl = new AbortController();
    const fetchOnce = vi
      .fn()
      .mockRejectedValue(new ApiError(404, { code: "rating.match_not_rated" }));
    const wait = vi.fn().mockImplementation(async () => ctrl.abort());
    await expect(
      pollMatchChanges(fetchOnce, { attempts: 10, delayMs: 10, wait, signal: ctrl.signal }),
    ).resolves.toBeNull();
    expect(fetchOnce).toHaveBeenCalledTimes(1);
  });

  it("уже отменённый сигнал — ни одного запроса", async () => {
    const ctrl = new AbortController();
    ctrl.abort();
    const fetchOnce = vi.fn();
    await expect(
      pollMatchChanges(fetchOnce, { attempts: 3, delayMs: 10, wait: vi.fn(), signal: ctrl.signal }),
    ).resolves.toBeNull();
    expect(fetchOnce).not.toHaveBeenCalled();
  });
});

describe("classifyBoardError", () => {
  const e = (status: number) => new ApiError(status, { code: `x.${status}` });
  it("404 — раздела нет; 401 — нужен вход", () => {
    expect(classifyBoardError(e(404), false)).toBe("notFound");
    expect(classifyBoardError(e(401), false)).toBe("login");
  });
  it("422: с курсором — устаревший курсор, без — некорректный раздел", () => {
    expect(classifyBoardError(e(422), true)).toBe("staleCursor");
    expect(classifyBoardError(e(422), false)).toBe("invalidLadder");
  });
  it("прочее — общая ошибка с повтором", () => {
    expect(classifyBoardError(e(503), false)).toBe("failed");
    expect(classifyBoardError(new Error("net"), false)).toBe("failed");
  });
});
