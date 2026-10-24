/**
 * Мок notifications (этап 5b) по `contracts/openapi/notifications.json`: список с курсором,
 * счётчик, «прочитано», настройки по типам, согласие на рассылку, отписка по токену.
 * Токены отписки: `good-token-0123456789` — 204, `limited-token-0123456789` — 429, `down-token-0123456789` — 503, любой другой — 422.
 * Realtime личного канала в e2e: `POST /api/notifications/__e2e/push` добавляет уведомление,
 * `GET /api/notifications/__e2e/feed?after=N` отдаёт ленту для подменного транспорта
 * (`e2e/realtime.ts`, `window.__GV_E2E_PERSONAL__`). Сценарий `notificationsDown` — 503.
 */
import { http, HttpResponse, type HttpHandler } from "msw";
import type { EmailSubscription, NotificationItem, TypeSetting } from "../../src/shared/api/types";
import { csrfOk, problem, subjectOf } from "./handlers";
import type { MockWorld } from "./world";

const NO_STORE = { "Cache-Control": "private, no-store" };

const seed = (): NotificationItem[] => [
  {
    id: "0192a000-0000-7000-a000-000000000003",
    type: "badge_unlocked",
    title: "Новый бейдж",
    body: "Вы получили бейдж «Первая победа»",
    created_at: "2026-10-24T09:00:00Z",
    read_at: null,
  },
  {
    id: "0192a000-0000-7000-a000-000000000002",
    type: "level_up",
    title: "Новый уровень",
    body: "Вы достигли 7 уровня",
    created_at: "2026-10-23T18:00:00Z",
    read_at: null,
  },
  {
    id: "0192a000-0000-7000-a000-000000000001",
    type: "avatar_decision",
    title: "Аватарка отклонена",
    body: "Причина: inappropriate",
    created_at: "2026-10-22T12:00:00Z",
    read_at: "2026-10-22T13:00:00Z",
  },
];

const seedSettings = (): TypeSetting[] => [
  { type: "level_up", title: "Новый уровень", in_app: true, email: false, user_mutable: true },
  { type: "badge_unlocked", title: "Новый бейдж", in_app: true, email: false, user_mutable: true },
  {
    type: "avatar_decision",
    title: "Решение по аватарке",
    in_app: true,
    email: false,
    user_mutable: false,
  },
];

export class NotificationsWorld {
  items = seed();
  settings = seedSettings();
  email: EmailSubscription = { subscribed: false, changed_at: null };
  /** Лента для подменного realtime: номер → уведомление. */
  feed: { n: number; item: NotificationItem }[] = [];
  unsubscribeBodies: unknown[] = [];
  private seq = 100;

  reset(): void {
    this.items = seed();
    this.settings = seedSettings();
    this.email = { subscribed: false, changed_at: null };
    this.feed = [];
    this.unsubscribeBodies = [];
    this.seq = 100;
  }

  unread(): number {
    return this.items.filter((i) => i.read_at === null).length;
  }

  push(
    title = "Новое задание",
    body = "Задание выполнено",
    type = "quest_completed",
    data?: Record<string, string>,
  ): NotificationItem {
    this.seq += 1;
    const item: NotificationItem = {
      id: `0192a000-0000-7000-a000-${String(this.seq).padStart(12, "0")}`,
      type,
      title,
      body,
      created_at: new Date().toISOString(),
      read_at: null,
      ...(data ? { data } : {}),
    };
    this.items.unshift(item);
    this.feed.push({ n: this.feed.length + 1, item });
    return item;
  }
}

function userOnly(request: Request, world: MockWorld): Response | null {
  if (world.scenario.notificationsDown) return problem(503, "notifications.unavailable");
  const subject = subjectOf(request);
  if (!subject) return problem(401, "auth.unauthenticated");
  if (!subject.startsWith("user.")) return problem(403, "auth.guest_forbidden");
  return null;
}

export function createNotificationsHandlers(world: MockWorld): HttpHandler[] {
  const n = world.notifications;
  const base = "*/api/notifications/v1";
  return [
    http.get(`${base}/notifications/unread-count`, ({ request }) => {
      const err = userOnly(request, world);
      if (err) return err;
      return HttpResponse.json({ count: n.unread() }, { headers: NO_STORE });
    }),

    http.get(`${base}/notifications`, ({ request }) => {
      const err = userOnly(request, world);
      if (err) return err;
      const url = new URL(request.url);
      const limit = Number(url.searchParams.get("limit") ?? 20);
      const start = Number(url.searchParams.get("cursor") ?? 0);
      const items = n.items.slice(start, start + limit);
      const next = start + limit < n.items.length ? String(start + limit) : null;
      return HttpResponse.json(
        { items, next_cursor: next, unread: n.unread() },
        { headers: NO_STORE },
      );
    }),

    http.post(`${base}/notifications/read-all`, ({ request }) => {
      const err = userOnly(request, world);
      if (err) return err;
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      const unread = n.items.filter((i) => i.read_at === null);
      for (const i of unread) i.read_at = new Date().toISOString();
      return HttpResponse.json({ marked: unread.length }, { headers: NO_STORE });
    }),

    http.post(`${base}/notifications/:id/read`, ({ request, params }) => {
      const err = userOnly(request, world);
      if (err) return err;
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      const item = n.items.find((i) => i.id === String(params.id));
      if (!item) return problem(404, "notifications.not_found");
      const was = item.read_at === null;
      if (was) item.read_at = new Date().toISOString();
      return HttpResponse.json({ marked: was ? 1 : 0 }, { headers: NO_STORE });
    }),

    http.get(`${base}/notification-settings`, ({ request }) => {
      const err = userOnly(request, world);
      if (err) return err;
      return HttpResponse.json({ types: n.settings }, { headers: NO_STORE });
    }),

    http.put(`${base}/notification-settings/:type`, async ({ request, params }) => {
      const err = userOnly(request, world);
      if (err) return err;
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      const setting = n.settings.find((s) => s.type === String(params.type));
      if (!setting) return problem(404, "notifications.type_not_found");
      if (!setting.user_mutable) return problem(409, "notifications.type_not_mutable");
      const body = (await request.json()) as { in_app: boolean; email: boolean };
      setting.in_app = body.in_app;
      setting.email = body.email;
      return HttpResponse.json(setting, { headers: NO_STORE });
    }),

    http.get(`${base}/email-subscription`, ({ request }) => {
      const err = userOnly(request, world);
      if (err) return err;
      return HttpResponse.json(n.email, { headers: NO_STORE });
    }),

    http.put(`${base}/email-subscription`, async ({ request }) => {
      const err = userOnly(request, world);
      if (err) return err;
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      const { subscribed } = (await request.json()) as { subscribed: boolean };
      n.email = { subscribed, changed_at: new Date().toISOString() };
      return HttpResponse.json(n.email, { headers: NO_STORE });
    }),

    // Без входа: единственная ручка notifications, доступная аноиму.
    http.post(`${base}/unsubscribe`, async ({ request }) => {
      const { token } = (await request.json()) as { token: string };
      n.unsubscribeBodies.push(token);
      if (token === "good-token-0123456789") return new HttpResponse(null, { status: 204 });
      if (token === "limited-token-0123456789") {
        const res = problem(429, "rate_limited");
        res.headers.set("Retry-After", "30");
        return res;
      }
      if (token === "down-token-0123456789") return problem(503, "notifications.unavailable");
      return problem(422, "notifications.unsubscribe_invalid");
    }),

    // ------------------------------------------------ управление realtime в e2e
    http.post("*/api/notifications/__e2e/push", async ({ request }) => {
      const body = (await request.json().catch(() => ({}))) as {
        title?: string;
        body?: string;
        type?: string;
        data?: Record<string, string>;
      };
      return HttpResponse.json(n.push(body.title, body.body, body.type, body.data));
    }),

    http.get("*/api/notifications/__e2e/feed", ({ request }) => {
      const after = Number(new URL(request.url).searchParams.get("after") ?? 0);
      const messages = n.feed
        .filter((m) => m.n > after)
        .map((m) => ({ n: m.n, data: { type: "notification.created", notification: m.item } }));
      return HttpResponse.json({ messages, last: n.feed.length });
    }),
  ];
}
