/**
 * Уведомления (этап 5b): чтение и мутации — прямо в notifications через gateway
 * (`/api/notifications/v1`, ADR 0017); число для колокольчика на старте — bff `/v1/pages/shell`.
 * Мутации «прочитано» идемпотентны по природе и ключа не требуют.
 */
import { api, unwrap } from "@/shared/api/client";
import type {
  EmailSubscription,
  NotificationItem,
  NotificationPage,
  NotificationSettings,
  ShellPage,
  TypeSetting,
} from "@/shared/api/types";

export const PAGE_SIZE = 20;

export const shellQueryKey = ["page", "shell"] as const;
export const unreadQueryKey = ["notifications", "unread"] as const;
export const listQueryKey = ["notifications", "list"] as const;
export const settingsQueryKey = ["notifications", "settings"] as const;
export const emailQueryKey = ["notifications", "email"] as const;

/** Период страховочного опроса счётчика, мс (realtime может молчать). */
export const UNREAD_POLL_MS = 60_000;

export async function fetchShell(): Promise<ShellPage> {
  return unwrap(await api().web.GET("/v1/pages/shell"));
}

export async function fetchUnreadCount(): Promise<number> {
  return unwrap(await api().notifications.GET("/v1/notifications/unread-count")).count;
}

export async function fetchNotifications(cursor: string | null): Promise<NotificationPage> {
  return unwrap(
    await api().notifications.GET("/v1/notifications", {
      params: { query: { limit: PAGE_SIZE, ...(cursor ? { cursor } : {}) } },
    }),
  );
}

export async function markRead(id: string): Promise<void> {
  unwrap(
    await api().notifications.POST("/v1/notifications/{notification_id}/read", {
      params: { path: { notification_id: id } },
    }),
  );
}

export async function markAllRead(): Promise<void> {
  unwrap(await api().notifications.POST("/v1/notifications/read-all"));
}

export async function fetchSettings(): Promise<NotificationSettings> {
  return unwrap(await api().notifications.GET("/v1/notification-settings"));
}

export async function saveSetting(
  type: string,
  value: { in_app: boolean; email: boolean },
): Promise<TypeSetting> {
  return unwrap(
    await api().notifications.PUT("/v1/notification-settings/{type_key}", {
      params: { path: { type_key: type } },
      body: value,
    }),
  );
}

export async function fetchEmailSubscription(): Promise<EmailSubscription> {
  return unwrap(await api().notifications.GET("/v1/email-subscription"));
}

export async function saveEmailSubscription(subscribed: boolean): Promise<EmailSubscription> {
  return unwrap(await api().notifications.PUT("/v1/email-subscription", { body: { subscribed } }));
}

/** Отписка по ссылке из письма: без входа; токен — только в теле запроса, не в адресе. */
export async function unsubscribe(token: string): Promise<void> {
  unwrap(await api().notifications.POST("/v1/unsubscribe", { body: { token } }));
}

export type { NotificationItem };
