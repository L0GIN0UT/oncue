import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, NetworkError } from "@/shared/api/errors";
import type { RoomState, TickResult } from "@/shared/api/types";
import type { RoomMessage } from "./message";
import type { RealtimeConfig } from "./config";
import { RoomLink, rateLimitDelayMs, type LinkStatus } from "./room-link";
import type { RoomTransport, TransportHandlers } from "./transport";

const NOW = "2026-10-22T10:00:00.000Z";

function state(version: number): RoomState {
  return { room: { version, server_now: NOW } as RoomState["room"], match: null, me: null };
}
const msg = (version: number, kind = "player"): RoomMessage => ({
  version,
  kind,
  server_now: NOW,
  delta: { user_id: "u", presence: "online" },
});

class FakeTransport implements RoomTransport {
  handlers: TransportHandlers | null = null;
  stopped = false;
  start(h: TransportHandlers) {
    this.handlers = h;
  }
  stop() {
    this.stopped = true;
  }
  subscribe(transport: "websocket" | "http_stream" = "websocket", recovered = false) {
    this.handlers!.onSubscribed({ transport, recovered });
  }
  publish(m: unknown) {
    this.handlers!.onPublication(m);
  }
  lose() {
    this.handlers!.onLost();
  }
}

function setup(
  opts: { stateVersion?: number; transport?: boolean; config?: Partial<RealtimeConfig> } = {},
) {
  let serverVersion = opts.stateVersion ?? 1;
  const transport = opts.transport === false ? null : new FakeTransport();
  const fetchState = vi.fn(async () => state(serverVersion));
  const tick = vi.fn(async (known: number | null): Promise<TickResult> => ({
    server_now: NOW,
    version: serverVersion,
    state: known === null || serverVersion > known ? state(serverVersion) : null,
  }));
  const states: number[] = [];
  const messages: number[] = [];
  const statuses: LinkStatus[] = [];
  const onFatal = vi.fn();
  const link = new RoomLink({
    transport,
    fetchState,
    tick,
    onState: (s) => states.push(s.room.version),
    onMessage: (m) => messages.push(m.version),
    onStatus: (s) => statuses.push(s),
    onFatal,
    // Ограничение частоты снимков проверяют отдельные тесты; остальные ждут снимок сразу.
    config: { resyncMinMs: 0, ...opts.config },
  });
  return {
    link,
    transport: transport as FakeTransport,
    fetchState,
    tick,
    states,
    messages,
    statuses,
    onFatal,
    setServerVersion: (v: number) => (serverVersion = v),
  };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("RoomLink: версии и ресинк", () => {
  it("старт: снимок, затем сообщения по порядку", async () => {
    const t = setup({ stateVersion: 3 });
    t.link.start();
    await vi.advanceTimersByTimeAsync(0);
    t.transport.subscribe();
    await vi.advanceTimersByTimeAsync(0);
    t.transport.publish(msg(4));
    t.transport.publish(msg(4)); // повтор — пропуск
    t.transport.publish(msg(5));
    expect(t.states.at(-1)).toBe(3);
    expect(t.messages).toEqual([4, 5]);
    expect(t.link.version).toBe(5);
    t.link.stop();
  });

  it("пропуск версии → GET state, буфер применяется поверх снимка", async () => {
    const t = setup({ stateVersion: 1 });
    t.link.start();
    t.transport.subscribe("websocket", true);
    await vi.advanceTimersByTimeAsync(0);
    expect(t.fetchState).toHaveBeenCalledTimes(1);
    t.setServerVersion(3);
    t.transport.publish(msg(3)); // версия 2 потеряна
    t.transport.publish(msg(4));
    await vi.advanceTimersByTimeAsync(0);
    expect(t.fetchState).toHaveBeenCalledTimes(2);
    expect(t.states).toEqual([1, 3]);
    expect(t.messages).toEqual([4]);
    expect(t.link.version).toBe(4);
    t.link.stop();
  });

  it("несколько дыр подряд — один ресинк в полёте", async () => {
    const t = setup({ stateVersion: 1 });
    t.link.start();
    await vi.advanceTimersByTimeAsync(0);
    t.setServerVersion(6);
    t.transport.publish(msg(4));
    t.transport.publish(msg(7));
    t.transport.publish(msg(8));
    await vi.advanceTimersByTimeAsync(0);
    expect(t.fetchState).toHaveBeenCalledTimes(2);
    expect(t.messages).toEqual([7, 8]);
    t.link.stop();
  });

  it("явный ресинк во время ресинка — ещё один снимок после текущего", async () => {
    const t = setup({ stateVersion: 1 });
    t.link.start();
    void t.link.resync();
    await vi.advanceTimersByTimeAsync(0);
    expect(t.fetchState).toHaveBeenCalledTimes(2);
    t.link.stop();
  });

  it("подписка без recovery (переподключение) — ресинк", async () => {
    const t = setup({ stateVersion: 2 });
    t.link.start();
    t.transport.subscribe("websocket", true);
    await vi.advanceTimersByTimeAsync(0);
    expect(t.fetchState).toHaveBeenCalledTimes(1);
    t.transport.lose();
    t.transport.subscribe("websocket", false);
    await vi.advanceTimersByTimeAsync(0);
    expect(t.fetchState).toHaveBeenCalledTimes(2);
    t.link.stop();
  });

  it("битый конверт и неизвестный kind не ломают поток", async () => {
    const t = setup({ stateVersion: 1 });
    t.link.start();
    await vi.advanceTimersByTimeAsync(0);
    t.transport.publish({ version: "2", kind: "player" });
    t.transport.publish(msg(2, "future_kind"));
    t.transport.publish(msg(3));
    expect(t.messages).toEqual([2, 3]);
    t.link.stop();
  });

  it("202: если дельта не пришла за acceptedResyncMs — ресинк", async () => {
    const t = setup({ stateVersion: 4 });
    t.link.start();
    t.transport.subscribe("websocket", true);
    await vi.advanceTimersByTimeAsync(0);
    t.link.awaitAccepted(4);
    await vi.advanceTimersByTimeAsync(1_500);
    expect(t.fetchState).toHaveBeenCalledTimes(2);
    // Дельта пришла вовремя — ресинка нет.
    t.link.awaitAccepted(4);
    t.transport.publish(msg(5));
    await vi.advanceTimersByTimeAsync(1_500);
    expect(t.fetchState).toHaveBeenCalledTimes(2);
    t.link.stop();
  });
});

describe("RoomLink: tick и обрывы", () => {
  it("с WebSocket tick раз в 5 с, без канала — раз в 1 с", async () => {
    const t = setup();
    t.link.start();
    await vi.advanceTimersByTimeAsync(0);
    t.transport.subscribe("websocket", true);
    await vi.advanceTimersByTimeAsync(4_900);
    expect(t.tick).toHaveBeenCalledTimes(0);
    await vi.advanceTimersByTimeAsync(100);
    expect(t.tick).toHaveBeenCalledTimes(1);
    t.transport.lose();
    await vi.advanceTimersByTimeAsync(3_000);
    expect(t.tick).toHaveBeenCalledTimes(4);
    expect(t.statuses.map((s) => s.transport)).toEqual(["websocket", null]);
    t.link.stop();
  });

  it("HTTP-stream вместо WebSocket — частый tick", async () => {
    const t = setup();
    t.link.start();
    t.transport.subscribe("http_stream", true);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(t.tick).toHaveBeenCalledTimes(2);
    t.link.stop();
  });

  it("без канала состояние приходит из tick (фолбэк снимка)", async () => {
    const t = setup({ stateVersion: 1, transport: false });
    t.link.start();
    await vi.advanceTimersByTimeAsync(0);
    t.setServerVersion(6);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(t.tick).toHaveBeenLastCalledWith(1);
    expect(t.states).toEqual([1, 6]);
    expect(t.link.version).toBe(6);
    t.link.stop();
  });

  it("баннер «нет связи» — после 20 с без канала и tick; снимается при первом ответе", async () => {
    const t = setup({ transport: false });
    t.fetchState.mockRejectedValue(new NetworkError());
    t.tick.mockRejectedValue(new NetworkError());
    t.link.start();
    await vi.advanceTimersByTimeAsync(19_900);
    expect(t.statuses.some((s) => s.offline)).toBe(false);
    await vi.advanceTimersByTimeAsync(200);
    expect(t.statuses.at(-1)).toEqual({ transport: null, offline: true });
    t.tick.mockResolvedValue({ server_now: NOW, version: 1, state: state(1) });
    await vi.advanceTimersByTimeAsync(1_000);
    expect(t.statuses.at(-1)).toEqual({ transport: null, offline: false });
    t.link.stop();
  });

  it("tick держит связь, даже когда канал молчит", async () => {
    const t = setup();
    t.link.start();
    t.transport.subscribe("websocket", true);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(t.statuses.some((s) => s.offline)).toBe(false);
    t.link.stop();
  });

  it("часы сервера — по server_now ответов", async () => {
    vi.setSystemTime(new Date("2026-10-22T09:59:00.000Z"));
    const t = setup();
    t.link.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(Math.round(t.link.clock.offset() / 1000)).toBe(60);
    t.link.stop();
  });

  it("401 на tick — тик повторяется сразу, а не теряется до следующего периода", async () => {
    const t = setup();
    t.link.start();
    await vi.advanceTimersByTimeAsync(0);
    const calls = t.tick.mock.calls.length;
    t.tick.mockRejectedValueOnce(new ApiError(401, { code: "auth.token_expired" }));
    await vi.advanceTimersByTimeAsync(1_000); // один период fallback-тика
    expect(t.tick.mock.calls.length - calls).toBe(2); // неудачный и немедленный повтор
    expect(t.onFatal).not.toHaveBeenCalled();
    t.link.stop();
  });

  it("401 дважды подряд — не «исключён из комнаты»: связь живёт, ждём следующий период", async () => {
    const t = setup();
    t.link.start();
    await vi.advanceTimersByTimeAsync(0);
    t.tick.mockRejectedValue(new ApiError(401, { code: "auth.unauthenticated" }));
    await vi.advanceTimersByTimeAsync(3_000);
    expect(t.onFatal).not.toHaveBeenCalled();
    expect(t.transport.stopped).toBe(false);
    t.tick.mockImplementation(async () => ({ server_now: NOW, version: 1, state: null }));
    await vi.advanceTimersByTimeAsync(2_000);
    expect(t.onFatal).not.toHaveBeenCalled();
    t.link.stop();
  });

  it("403/404 — связь останавливается, onFatal", async () => {
    const t = setup();
    t.link.start();
    await vi.advanceTimersByTimeAsync(0);
    t.tick.mockRejectedValue(new ApiError(404, { code: "match.room_not_found" }));
    await vi.advanceTimersByTimeAsync(1_000);
    expect(t.onFatal).toHaveBeenCalledOnce();
    expect(t.transport.stopped).toBe(true);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(t.tick).toHaveBeenCalledTimes(1);
  });

  it("устаревший снимок команды не откатывает канал", async () => {
    const t = setup({ stateVersion: 5 });
    t.link.start();
    await vi.advanceTimersByTimeAsync(0);
    t.link.acceptSnapshot(state(3));
    expect(t.states).toEqual([5]);
    t.link.acceptSnapshot(state(7));
    expect(t.states).toEqual([5, 7]);
    t.link.stop();
  });

  it("гонка входа: 403 на первый снимок (вход ответил 202) — повтор с паузой, без onFatal", async () => {
    const t = setup({ stateVersion: 2 });
    t.fetchState
      .mockRejectedValueOnce(new ApiError(403, { code: "match.not_participant" }))
      .mockRejectedValueOnce(new ApiError(403, { code: "match.not_participant" }));
    t.link.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(t.onFatal).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1_300);
    expect(t.fetchState).toHaveBeenCalledTimes(3);
    expect(t.link.version).toBe(2);
    expect(t.onFatal).not.toHaveBeenCalled();
    t.link.stop();
  });

  it("гонка входа: повторы кончились — onFatal", async () => {
    const t = setup();
    t.fetchState.mockRejectedValue(new ApiError(403, { code: "match.not_participant" }));
    t.tick.mockRejectedValue(new ApiError(403, { code: "match.not_participant" }));
    t.link.start();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(t.fetchState).toHaveBeenCalledTimes(6);
    expect(t.onFatal).toHaveBeenCalledOnce();
  });
});

describe("RoomLink: частота GET /state и retry_after", () => {
  const flush = () => vi.advanceTimersByTimeAsync(0);

  it("серия просьб ресинка за окно — один запрос сразу и один после окна", async () => {
    const t = setup({ transport: false, config: { resyncMinMs: 1000 } });
    t.link.start();
    await flush();
    expect(t.fetchState).toHaveBeenCalledTimes(1);
    for (let i = 0; i < 5; i++) void t.link.resync();
    await vi.advanceTimersByTimeAsync(500);
    expect(t.fetchState).toHaveBeenCalledTimes(1); // окно ещё не прошло
    await vi.advanceTimersByTimeAsync(600);
    expect(t.fetchState).toHaveBeenCalledTimes(2); // пять просьб слиты в один запрос
    t.link.stop();
  });

  it("429 с Retry-After: следующий снимок не раньше, потом повтор и успех", async () => {
    const t = setup({ transport: false, config: { resyncMinMs: 100 } });
    t.fetchState.mockRejectedValueOnce(new ApiError(429, { code: "http.429" }, 7));
    t.link.start();
    await flush();
    expect(t.fetchState).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(6_900);
    expect(t.fetchState).toHaveBeenCalledTimes(1); // ждём retry_after
    await vi.advanceTimersByTimeAsync(200);
    expect(t.fetchState).toHaveBeenCalledTimes(2);
    expect(t.states.at(-1)).toBe(1);
    t.link.stop();
  });

  it("429 без заголовка: retry_after берётся из details; без значения — окно resyncMinMs", async () => {
    expect(rateLimitDelayMs(new ApiError(429, { code: "x", details: { retry_after: 3 } }))).toBe(
      3000,
    );
    expect(rateLimitDelayMs(new ApiError(429, { code: "x" }))).toBe(0);
    expect(rateLimitDelayMs(new ApiError(500, { code: "x" }))).toBeNull();
    expect(rateLimitDelayMs(new NetworkError())).toBeNull();
  });

  it("429 повторяется не бесконечно: после лимита повторов снимок приносит tick", async () => {
    const t = setup({ config: { resyncMinMs: 100, resyncRateLimitRetries: 2 } });
    t.fetchState.mockRejectedValue(new ApiError(429, { code: "http.429" }, 1));
    t.link.start();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(t.fetchState).toHaveBeenCalledTimes(3); // первый + 2 повтора
    t.link.stop();
  });

  it("после последнего повтора 429 окно остаётся сдвинутым: tick не бьёт в /state сразу", async () => {
    const t = setup({ config: { resyncMinMs: 100, resyncRateLimitRetries: 0 } });
    t.fetchState.mockRejectedValue(new ApiError(429, { code: "http.429" }, 10));
    t.link.start();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(t.fetchState).toHaveBeenCalledTimes(1);
    void t.link.resync(); // просьба внутри окна retry_after
    await vi.advanceTimersByTimeAsync(4_000);
    expect(t.fetchState).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(t.fetchState).toHaveBeenCalledTimes(2);
    t.link.stop();
  });

  it("огромный Retry-After не вешает ресинк дольше потолка", async () => {
    const t = setup({
      transport: false,
      config: { resyncMinMs: 100, resyncMaxWaitMs: 5_000, resyncRateLimitRetries: 1 },
    });
    t.fetchState.mockRejectedValueOnce(new ApiError(429, { code: "http.429" }, 3600));
    t.link.start();
    await vi.advanceTimersByTimeAsync(4_900);
    expect(t.fetchState).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(300);
    expect(t.fetchState).toHaveBeenCalledTimes(2);
    t.link.stop();
  });

  it("429, затем 403: комната закрыта — onFatal, без новых запросов", async () => {
    const t = setup({ transport: false, config: { resyncMinMs: 100 } });
    t.fetchState
      .mockRejectedValueOnce(new ApiError(429, { code: "http.429" }, 1))
      .mockRejectedValue(new ApiError(403, { code: "match.not_participant" }));
    t.link.start();
    await vi.advanceTimersByTimeAsync(20_000);
    expect(t.onFatal).toHaveBeenCalledTimes(1);
  });

  it("stop и start во время ожидания: старый цикл не оживает, запросов не больше нужного", async () => {
    const t = setup({ transport: false, config: { resyncMinMs: 1000 } });
    t.link.start();
    await flush();
    void t.link.resync(); // ждёт окно
    t.link.stop();
    t.link.start();
    await vi.advanceTimersByTimeAsync(1_200);
    // Первый запуск — 1 запрос; после перезапуска — один запрос нового цикла (старый не ожил).
    expect(t.fetchState.mock.calls.length).toBeLessThanOrEqual(3);
    const before = t.fetchState.mock.calls.length;
    await vi.advanceTimersByTimeAsync(300);
    expect(t.fetchState.mock.calls.length).toBe(before);
    t.link.stop();
  });

  it("stop во время ожидания окна не оставляет запросов", async () => {
    const t = setup({ transport: false, config: { resyncMinMs: 1000 } });
    t.link.start();
    await flush();
    void t.link.resync();
    t.link.stop();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(t.fetchState).toHaveBeenCalledTimes(1);
  });
});
