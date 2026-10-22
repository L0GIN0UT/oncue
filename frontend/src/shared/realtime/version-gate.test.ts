import { describe, expect, it } from "vitest";
import type { RoomMessage } from "./message";
import { VersionGate } from "./version-gate";

const msg = (version: number, kind = "player"): RoomMessage => ({
  version,
  kind,
  server_now: "2026-10-22T10:00:00Z",
  delta: {},
});
const versions = (ms: RoomMessage[]) => ms.map((m) => m.version);

describe("VersionGate", () => {
  it("применяет подряд идущие версии", () => {
    const gate = new VersionGate();
    gate.reset(5);
    expect(versions(gate.offer(msg(6)).apply)).toEqual([6]);
    expect(versions(gate.offer(msg(7)).apply)).toEqual([7]);
    expect(gate.version).toBe(7);
  });

  it("повтор и устаревшие версии пропускает без ресинка", () => {
    const gate = new VersionGate();
    gate.reset(5);
    expect(gate.offer(msg(5))).toEqual({ apply: [], resync: false });
    expect(gate.offer(msg(3))).toEqual({ apply: [], resync: false });
    expect(gate.version).toBe(5);
  });

  it("дыра — ресинк; после снимка буфер дотягивается по порядку", () => {
    const gate = new VersionGate();
    gate.reset(5);
    expect(gate.offer(msg(8))).toEqual({ apply: [], resync: true });
    expect(gate.offer(msg(9)).resync).toBe(true);
    expect(gate.hasGap).toBe(true);
    // Снимок версии 7 закрывает дыру: 8 и 9 применяются поверх.
    expect(versions(gate.reset(7)!)).toEqual([8, 9]);
    expect(gate.version).toBe(9);
    expect(gate.hasGap).toBe(false);
  });

  it("недостающая версия, пришедшая позже, тоже закрывает дыру", () => {
    const gate = new VersionGate();
    gate.reset(1);
    gate.offer(msg(3));
    expect(versions(gate.offer(msg(2)).apply)).toEqual([2, 3]);
  });

  it("снимок новее буфера выбрасывает устаревшие сообщения", () => {
    const gate = new VersionGate();
    gate.reset(1);
    gate.offer(msg(3));
    gate.offer(msg(4));
    expect(gate.reset(10)).toEqual([]);
    expect(gate.version).toBe(10);
    expect(gate.hasGap).toBe(false);
  });

  it("устаревший снимок (медленный ответ) не откатывает состояние", () => {
    const gate = new VersionGate();
    gate.reset(10);
    expect(gate.reset(8)).toBeNull();
    expect(gate.version).toBe(10);
  });

  it("до первого снимка сообщения ждут в буфере", () => {
    const gate = new VersionGate();
    expect(gate.offer(msg(4))).toEqual({ apply: [], resync: false });
    expect(versions(gate.reset(3)!)).toEqual([4]);
  });

  it("неизвестный kind засчитывает версию (расширение контракта совместимо)", () => {
    const gate = new VersionGate();
    gate.reset(1);
    expect(versions(gate.offer(msg(2, "future_kind")).apply)).toEqual([2]);
    expect(versions(gate.offer(msg(3)).apply)).toEqual([3]);
  });

  it("буфер ограничен: держит самые новые", () => {
    const gate = new VersionGate(2);
    gate.reset(1);
    gate.offer(msg(5));
    gate.offer(msg(6));
    gate.offer(msg(7));
    expect(versions(gate.reset(5)!)).toEqual([6, 7]);
  });
});
