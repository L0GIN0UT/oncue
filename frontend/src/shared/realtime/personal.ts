/**
 * Личный канал `personal:#<user_id>` (ADR 0035, 0047): Centrifugo user-limited channel — подписка
 * разрешена только владельцу соединения, поэтому токен подписки не нужен, достаточно
 * connection-токена (identity). Сообщение `notification.created` —
 * `contracts/realtime/notification.created.v1.json`. Запись в БД первична: пропущенное клиент
 * догоняет списком и опросом счётчика, поэтому recovery здесь не требуется.
 */
import { Centrifuge, UnauthorizedError, type Subscription } from "centrifuge";
import type { NotificationItem } from "@/shared/api/types";
import { centrifugeEndpoints, isStopError } from "./transport";
import { fetchConnectionToken } from "./tokens";

export const personalChannel = (userId: string) => `personal:#${userId}`;

export type PersonalHandlers = {
  /** Новое уведомление (уже проверенное по контракту). */
  onNotification(item: NotificationItem): void;
};

export type PersonalTransport = { start(handlers: PersonalHandlers): void; stop(): void };
export type PersonalTransportFactory = (userId: string) => PersonalTransport;

const str = (v: unknown): v is string => typeof v === "string";

/** Разбор сообщения канала; чужие и битые сообщения — `null`. Лишние поля уведомления отбрасываются. */
export function parseNotificationMessage(data: unknown): NotificationItem | null {
  if (!data || typeof data !== "object") return null;
  const msg = data as Record<string, unknown>;
  if (msg.type !== "notification.created") return null;
  const n = msg.notification;
  if (!n || typeof n !== "object") return null;
  const r = n as Record<string, unknown>;
  if (!str(r.id) || !r.id) return null;
  if (!str(r.type) || !r.type) return null;
  if (!str(r.title) || !r.title) return null;
  if (!str(r.body) || !r.body) return null;
  if (!str(r.created_at)) return null;
  if (r.read_at !== null && !str(r.read_at)) return null;
  return {
    id: r.id,
    type: r.type,
    title: r.title,
    body: r.body,
    created_at: r.created_at,
    read_at: r.read_at,
  };
}

export function createCentrifugePersonalTransport(userId: string): PersonalTransport {
  let client: Centrifuge | null = null;
  let sub: Subscription | null = null;
  return {
    start(handlers) {
      const { transports, emulationEndpoint } = centrifugeEndpoints(globalThis.location.origin);
      client = new Centrifuge(transports, {
        emulationEndpoint,
        getToken: async () => {
          try {
            return await fetchConnectionToken();
          } catch (e) {
            // 401/403/503 realtime.not_configured: без повторов, счётчик живёт на опросе.
            if (isStopError(e)) throw new UnauthorizedError("stop");
            throw e;
          }
        },
      });
      sub = client.newSubscription(personalChannel(userId));
      sub.on("publication", (ctx) => {
        const item = parseNotificationMessage(ctx.data);
        if (item) handlers.onNotification(item);
      });
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
 * Подмена для e2e на моках (`window.__GV_E2E_PERSONAL__`, ставится `addInitScript`): без
 * Centrifugo в окружении тестов, как `__GV_E2E_REALTIME__` комнаты.
 */
type E2EWindow = { __GV_E2E_PERSONAL__?: PersonalTransportFactory };

export function defaultPersonalFactory(): PersonalTransportFactory {
  return (globalThis as E2EWindow).__GV_E2E_PERSONAL__ ?? createCentrifugePersonalTransport;
}
