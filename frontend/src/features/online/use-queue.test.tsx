import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { ApiError, NetworkError } from "@/shared/api/errors";
import type { QueueTicket } from "@/shared/api/types";
import { enterQueue, leaveQueue, tickQueue } from "@/features/match/queue-api";
import { QUEUE_REMOVED_KEY, QUEUE_SESSION_EXPIRED_KEY } from "./queue-state";
import { useQueue } from "./use-queue";

vi.mock("@/features/match/queue-api", () => ({
  enterQueue: vi.fn(),
  tickQueue: vi.fn(),
  leaveQueue: vi.fn(),
}));

const enter = vi.mocked(enterQueue);
const tick = vi.mocked(tickQueue);
const leave = vi.mocked(leaveQueue);

const ticket = (patch: Partial<QueueTicket> = {}): QueueTicket => ({
  ladder: "film",
  format: "classic",
  state: "searching",
  entered_at: "2026-10-23T10:00:00Z",
  wait_s: 0,
  window: 100,
  slow: false,
  room_code: null,
  tick_s: 5,
  ...patch,
});
const problem = (status: number, code: string) => new ApiError(status, { code });
const notInQueue = () => problem(404, "match.not_in_queue");

/** Прокрутить таймеры и дать выполниться промисам. */
const advance = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));

/** Хук после resume-тика (записи нет) и входа в очередь: состояние searching. */
async function searching() {
  tick.mockRejectedValueOnce(notInQueue());
  enter.mockResolvedValueOnce(ticket());
  const hook = renderHook(() => useQueue());
  await advance(0);
  await act(async () => {
    await hook.result.current.enter("film", "classic");
  });
  expect(hook.result.current.state.phase).toBe("searching");
  tick.mockClear();
  return hook;
}

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers();
  enter.mockReset();
  tick.mockReset();
  leave.mockReset();
  leave.mockResolvedValue(undefined);
});
afterEach(() => {
  setHidden(false);
  vi.useRealTimers();
});

describe("useQueue — heartbeat", () => {
  it("resume: запись, найденная при загрузке, — searching; 404 при загрузке молча idle", async () => {
    tick.mockResolvedValueOnce(ticket({ wait_s: 12 }));
    const a = renderHook(() => useQueue());
    await advance(0);
    expect(a.result.current.state).toMatchObject({ phase: "searching", waitS: 12 });
    a.unmount();

    tick.mockRejectedValueOnce(notInQueue());
    const b = renderHook(() => useQueue());
    await advance(0);
    expect(b.result.current.state.phase).toBe("idle");
  });

  it("период heartbeat — tick_s из ответа сервера", async () => {
    const { result } = await searching();
    tick.mockResolvedValue(ticket({ tick_s: 5, wait_s: 5 }));
    await advance(4900);
    expect(tick).not.toHaveBeenCalled();
    await advance(200);
    expect(tick).toHaveBeenCalledTimes(1);
    expect(result.current.state).toMatchObject({ waitS: 5 });
  });

  it("сервер не сообщил tick_s — интервала нет (запуск без heartbeat)", async () => {
    tick.mockRejectedValueOnce(notInQueue());
    enter.mockResolvedValueOnce(ticket({ tick_s: null }));
    const { result } = renderHook(() => useQueue());
    await advance(0);
    await act(async () => {
      await result.current.enter("film", "classic");
    });
    tick.mockClear();
    await advance(60_000);
    expect(tick).not.toHaveBeenCalled();
  });

  it("404 на tick во время поиска → «вас убрали из очереди»; heartbeat останавливается", async () => {
    const { result } = await searching();
    tick.mockRejectedValue(notInQueue());
    await advance(5000);
    expect(result.current.state).toEqual({
      phase: "error",
      errorKey: QUEUE_REMOVED_KEY,
      roomCode: null,
    });
    tick.mockClear();
    await advance(20_000);
    expect(tick).not.toHaveBeenCalled();
  });

  it.each([401, 403])("%i на tick → «сессия истекла», остановка", async (status) => {
    const { result } = await searching();
    tick.mockRejectedValue(problem(status, "auth.unauthenticated"));
    await advance(5000);
    expect(result.current.state).toEqual({
      phase: "error",
      errorKey: QUEUE_SESSION_EXPIRED_KEY,
      roomCode: null,
    });
  });

  it("сбой сети и 429 на tick не прерывают поиск", async () => {
    const { result } = await searching();
    tick.mockRejectedValueOnce(new NetworkError()).mockRejectedValueOnce(problem(429, "x"));
    await advance(10_000);
    expect(tick).toHaveBeenCalledTimes(2);
    expect(result.current.state.phase).toBe("searching");
  });

  it("найден матч → matched, heartbeat не продолжается", async () => {
    const { result } = await searching();
    tick.mockResolvedValue(ticket({ state: "matched", room_code: "K7MQ2XWP" }));
    await advance(5000);
    expect(result.current.state).toMatchObject({ phase: "matched", roomCode: "K7MQ2XWP" });
    tick.mockClear();
    await advance(20_000);
    expect(tick).not.toHaveBeenCalled();
  });
});

describe("useQueue — фоновая вкладка", () => {
  it("возврат на вкладку: tick сразу, не дожидаясь интервала; уход в фон — не тикает", async () => {
    const { result } = await searching();
    tick.mockResolvedValue(ticket({ wait_s: 90, slow: true }));
    act(() => setHidden(true));
    await advance(0);
    expect(tick).not.toHaveBeenCalled();
    act(() => setHidden(false));
    await advance(0);
    expect(tick).toHaveBeenCalledTimes(1);
    expect(result.current.state).toMatchObject({ waitS: 90, slow: true });
  });

  it("запись истекла, пока вкладка спала: после возврата честное сообщение", async () => {
    const { result } = await searching();
    tick.mockRejectedValue(notInQueue());
    act(() => setHidden(true));
    act(() => setHidden(false));
    await advance(0);
    expect(result.current.state).toMatchObject({ phase: "error", errorKey: QUEUE_REMOVED_KEY });
  });

  it("вне поиска возврат на вкладку tick не шлёт", async () => {
    tick.mockRejectedValueOnce(notInQueue());
    renderHook(() => useQueue());
    await advance(0);
    tick.mockClear();
    act(() => setHidden(true));
    act(() => setHidden(false));
    await advance(0);
    expect(tick).not.toHaveBeenCalled();
  });
});

describe("useQueue — отмена", () => {
  it("успех: idle; 404 not_in_queue — тоже успех", async () => {
    const a = await searching();
    await act(async () => {
      await a.result.current.leave();
    });
    expect(a.result.current.state.phase).toBe("idle");
    a.unmount();

    const b = await searching();
    leave.mockRejectedValueOnce(notInQueue());
    await act(async () => {
      await b.result.current.leave();
    });
    expect(b.result.current.state.phase).toBe("idle");
  });

  it("провал leave: запись жива — остаёмся в поиске с сообщением, heartbeat идёт", async () => {
    const { result } = await searching();
    leave.mockRejectedValueOnce(problem(503, "match.game_unavailable"));
    await act(async () => {
      await result.current.leave();
    });
    expect(result.current.state).toMatchObject({
      phase: "searching",
      leaving: false,
      notice: expect.any(String),
    });
    tick.mockResolvedValue(ticket());
    await advance(5000);
    expect(tick).toHaveBeenCalledTimes(1);
    expect(result.current.state).toMatchObject({ phase: "searching", notice: null });
  });

  it("409 queue_matching: повтор через секунду, затем выход", async () => {
    const { result } = await searching();
    leave.mockRejectedValueOnce(problem(409, "match.queue_matching"));
    let done = false;
    act(() => {
      void result.current.leave().then(() => (done = true));
    });
    await advance(0);
    expect(leave).toHaveBeenCalledTimes(1);
    expect(result.current.state).toMatchObject({ phase: "searching", leaving: true });
    await advance(1000);
    expect(leave).toHaveBeenCalledTimes(2);
    expect(done).toBe(true);
    expect(result.current.state.phase).toBe("idle");
  });

  it("queue_matching не проходит три раза подряд — сообщение, поиск продолжается", async () => {
    const { result } = await searching();
    leave.mockRejectedValue(problem(409, "match.queue_matching"));
    act(() => void result.current.leave());
    await advance(2100);
    expect(leave).toHaveBeenCalledTimes(3);
    expect(result.current.state).toMatchObject({ phase: "searching", leaving: false });
  });

  it("размонтирование отменяет повтор leave", async () => {
    const { result, unmount } = await searching();
    leave.mockRejectedValue(problem(409, "match.queue_matching"));
    act(() => void result.current.leave());
    await advance(0);
    expect(leave).toHaveBeenCalledTimes(1);
    unmount();
    // Размонтирование само снимает с очереди одним вызовом; повторов от retry-таймера нет.
    const afterUnmount = leave.mock.calls.length;
    await advance(5000);
    expect(leave).toHaveBeenCalledTimes(afterUnmount);
  });

  it("гонка: tick, начатый до отмены, не возвращает игрока в поиск", async () => {
    const { result } = await searching();
    let resolveTick!: (t: QueueTicket) => void;
    tick.mockReturnValueOnce(new Promise<QueueTicket>((r) => (resolveTick = r)));
    await advance(5000);
    expect(tick).toHaveBeenCalledTimes(1);
    await act(async () => {
      await result.current.leave();
    });
    expect(result.current.state.phase).toBe("idle");
    await act(async () => resolveTick(ticket()));
    expect(result.current.state.phase).toBe("idle");
  });

  it("во время отмены новые tick не стартуют", async () => {
    const { result } = await searching();
    let finishLeave!: () => void;
    leave.mockReturnValueOnce(new Promise<void>((r) => (finishLeave = r)));
    act(() => void result.current.leave());
    await advance(15_000);
    expect(tick).not.toHaveBeenCalled();
    await act(async () => finishLeave());
    expect(result.current.state.phase).toBe("idle");
  });
});

describe("useQueue — размонтирование", () => {
  it("в поиске снимает игрока с очереди", async () => {
    const { unmount } = await searching();
    unmount();
    expect(leave).toHaveBeenCalledTimes(1);
  });

  it("в idle очередь не трогает", async () => {
    tick.mockRejectedValueOnce(notInQueue());
    const { unmount } = renderHook(() => useQueue());
    await advance(0);
    unmount();
    expect(leave).not.toHaveBeenCalled();
  });

  it("в entering: ответ enter после размонтирования — сразу leave, без диспатча", async () => {
    tick.mockRejectedValueOnce(notInQueue());
    let resolveEnter!: (t: QueueTicket) => void;
    enter.mockReturnValueOnce(new Promise<QueueTicket>((r) => (resolveEnter = r)));
    const { result, unmount } = renderHook(() => useQueue());
    await advance(0);
    act(() => void result.current.enter("film", "classic"));
    expect(result.current.state.phase).toBe("entering");
    unmount();
    // Запись ещё не создана ответом — leave на всякий случай уходит сразу и после ответа.
    await act(async () => resolveEnter(ticket()));
    expect(leave).toHaveBeenCalledTimes(2);
  });

  it("ошибка входа показывается ключом и кодом комнаты при конфликте", async () => {
    tick.mockRejectedValueOnce(notInQueue());
    enter.mockRejectedValueOnce(
      new ApiError(409, { code: "match.active_room_conflict", details: { code: "ABCD2345" } }),
    );
    const { result } = renderHook(() => useQueue());
    await advance(0);
    await act(async () => {
      await result.current.enter("film", "classic");
    });
    expect(result.current.state).toMatchObject({
      phase: "error",
      errorKey: "match.error.active_room_conflict",
      roomCode: "ABCD2345",
    });
  });
});
