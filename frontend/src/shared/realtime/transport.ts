/**
 * Транспорт канала комнаты. Боевой — Centrifugo через `centrifuge` (WebSocket с фолбэком на
 * HTTP-stream, ADR 0035); в тестах и e2e на моках — подмена с тем же интерфейсом.
 */
import { Centrifuge, UnauthorizedError, type Subscription } from "centrifuge";
import { isApiError } from "@/shared/api/errors";
import { fetchConnectionToken, fetchSubscriptionToken } from "./tokens";

/** Имя транспорта, на котором держится канал; `null` — канала нет. */
export type TransportKind = "websocket" | "http_stream" | null;

export type TransportHandlers = {
  /** Публикация канала (сырые данные, конверт разбирает `RoomLink`). */
  onPublication(data: unknown): void;
  /** Подписка активна. `recovered` — пропущенные сообщения будут досланы (recovery). */
  onSubscribed(info: { transport: TransportKind; recovered: boolean }): void;
  /** Канал потерян (обрыв, переподключение) — до нового `onSubscribed`. */
  onLost(): void;
};

export type RoomTransport = {
  start(handlers: TransportHandlers): void;
  stop(): void;
};

export type TransportFactory = (code: string) => RoomTransport;

/** Ошибка токена: 401/403 — без повторов (гость, не участник), иначе Centrifugo повторит. */
async function tokenOrStop(get: () => Promise<string>): Promise<string> {
  try {
    return await get();
  } catch (e) {
    if (isApiError(e) && (e.status === 401 || e.status === 403 || e.status === 404))
      throw new UnauthorizedError(e.code);
    throw e;
  }
}

/** Адреса Centrifugo на своём origin (gateway: `location /connection/`). */
export function centrifugeEndpoints(origin: string) {
  const ws = origin.replace(/^http/, "ws");
  return {
    transports: [
      { transport: "websocket" as const, endpoint: `${ws}/connection/websocket` },
      { transport: "http_stream" as const, endpoint: `${origin}/connection/http_stream` },
    ],
    emulationEndpoint: `${origin}/connection/emulation`,
  };
}

export function createCentrifugeTransport(code: string): RoomTransport {
  let client: Centrifuge | null = null;
  let sub: Subscription | null = null;
  return {
    start(handlers) {
      const { transports, emulationEndpoint } = centrifugeEndpoints(globalThis.location.origin);
      let transport: TransportKind = null;
      client = new Centrifuge(transports, {
        emulationEndpoint,
        getToken: () => tokenOrStop(fetchConnectionToken),
      });
      client.on("connected", (ctx) => {
        transport = ctx.transport === "websocket" ? "websocket" : "http_stream";
      });
      client.on("connecting", () => handlers.onLost());
      client.on("disconnected", () => handlers.onLost());
      sub = client.newSubscription(`room:${code}`, {
        getToken: () => tokenOrStop(() => fetchSubscriptionToken(code)),
      });
      sub.on("publication", (ctx) => handlers.onPublication(ctx.data));
      sub.on("subscribed", (ctx) =>
        handlers.onSubscribed({ transport, recovered: ctx.wasRecovering && ctx.recovered }),
      );
      sub.on("subscribing", () => handlers.onLost());
      sub.on("unsubscribed", () => handlers.onLost());
      sub.subscribe();
      client.connect();
    },
    stop() {
      sub?.unsubscribe();
      if (sub && client) client.removeSubscription(sub);
      client?.disconnect();
      sub = null;
      client = null;
    },
  };
}

/**
 * Подмена транспорта для e2e на моках (Playwright `addInitScript`): без Centrifugo в окружении
 * тестов. Сам по себе флаг ничего не открывает — скрипт на странице и так управляет клиентом.
 */
type E2EWindow = { __GV_E2E_REALTIME__?: TransportFactory };

export function defaultTransportFactory(): TransportFactory {
  const override = (globalThis as E2EWindow).__GV_E2E_REALTIME__;
  return override ?? createCentrifugeTransport;
}
