import { describe, expect, it } from "vitest";
import type { QueueTicket } from "@/shared/api/types";
import {
  initialQueue,
  QUEUE_REMOVED_KEY,
  queueReducer,
  type QueueAction,
  type QueueState,
} from "./queue-state";

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

const reduce = (s: QueueState, ...a: QueueAction[]) => a.reduce(queueReducer, s);

const searching = () =>
  reduce(
    initialQueue,
    { type: "enter", ladder: "film", format: "classic" },
    { type: "ticket", ticket: ticket() },
  );

describe("queueReducer", () => {
  it("вход: idle → entering → searching по ответу enter, период heartbeat — из ответа", () => {
    const s = reduce(initialQueue, { type: "enter", ladder: "film", format: "classic" });
    expect(s).toMatchObject({ phase: "entering", ladder: "film", format: "classic" });
    expect(reduce(s, { type: "ticket", ticket: ticket() })).toEqual({
      phase: "searching",
      ladder: "film",
      format: "classic",
      slow: false,
      waitS: 0,
      tickS: 5,
      leaving: false,
      notice: null,
    });
  });

  it("tick без tick_s не теряет известный период", () => {
    const s = reduce(searching(), { type: "ticket", ticket: ticket({ tick_s: null }) });
    expect(s).toMatchObject({ phase: "searching", tickS: 5 });
    // Период сервера побеждает: сервер сменил настройку.
    expect(reduce(s, { type: "ticket", ticket: ticket({ tick_s: 3 }) })).toMatchObject({
      tickS: 3,
    });
  });

  it("tick: подсказка «ищем дольше обычного» включается и держит время ожидания", () => {
    const s = reduce(searching(), { type: "ticket", ticket: ticket({ slow: true, wait_s: 45 }) });
    expect(s).toMatchObject({ phase: "searching", slow: true, waitS: 45 });
  });

  it("matched с кодом комнаты — финальное состояние, поздние тики его не меняют", () => {
    const matched = reduce(searching(), {
      type: "ticket",
      ticket: ticket({ state: "matched", room_code: "K7MQ2XWP" }),
    });
    expect(matched).toEqual({
      phase: "matched",
      ladder: "film",
      format: "classic",
      roomCode: "K7MQ2XWP",
    });
    expect(reduce(matched, { type: "ticket", ticket: ticket() })).toBe(matched);
  });

  it("matched без кода комнаты — продолжаем искать (код придёт со следующим tick)", () => {
    expect(
      reduce(searching(), { type: "ticket", ticket: ticket({ state: "matched" }) }).phase,
    ).toBe("searching");
  });

  it("отмена: leaving блокирует кнопку; провал выхода оставляет поиск с сообщением, tick его снимает", () => {
    const leaving = reduce(searching(), { type: "leaving" });
    expect(leaving).toMatchObject({ phase: "searching", leaving: true });
    const failed = reduce(leaving, { type: "leaveFailed", errorKey: "rating.queue.leaveFailed" });
    expect(failed).toMatchObject({
      phase: "searching",
      leaving: false,
      notice: "rating.queue.leaveFailed",
    });
    expect(reduce(failed, { type: "ticket", ticket: ticket() })).toMatchObject({ notice: null });
    expect(reduce(leaving, { type: "left" })).toEqual(initialQueue);
  });

  it("leaveFailed и leaving вне поиска ничего не меняют", () => {
    expect(reduce(initialQueue, { type: "leaving" })).toBe(initialQueue);
    expect(reduce(initialQueue, { type: "leaveFailed", errorKey: "x" })).toBe(initialQueue);
  });

  it("404 на heartbeat (gone) в поиске → сообщение «вас убрали из очереди»; вне поиска — тишина", () => {
    expect(reduce(searching(), { type: "gone" })).toEqual({
      phase: "error",
      errorKey: QUEUE_REMOVED_KEY,
      roomCode: null,
    });
    expect(reduce(initialQueue, { type: "gone" })).toBe(initialQueue);
  });

  it("ошибка входа → error с ключом и кодом комнаты", () => {
    expect(
      reduce(
        initialQueue,
        { type: "enter", ladder: "film", format: "classic" },
        { type: "failed", errorKey: "match.error.ranked_forbidden", roomCode: null },
      ),
    ).toEqual({ phase: "error", errorKey: "match.error.ranked_forbidden", roomCode: null });
  });

  it("resume: запись, найденная при загрузке хаба, сразу searching", () => {
    expect(reduce(initialQueue, { type: "ticket", ticket: ticket({ wait_s: 12 }) })).toMatchObject({
      phase: "searching",
      waitS: 12,
      ladder: "film",
      tickS: 5,
    });
  });
});
