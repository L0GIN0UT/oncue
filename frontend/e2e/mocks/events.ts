/**
 * Мок этапа 5d по контрактам `contracts/openapi/{progression,bff_web}.json`: сезонное событие
 * «Зимняя охота» (тема `winter`): блок `event` оболочки и главной, страница `/pages/events/{key}`,
 * архив в профиле и засчёт находки `POST /api/progression/v1/events/{key}/finds/{find}/claim`.
 *
 * Сценарии (`POST /__mock/scenario`, поля `Scenario`): `event: "active"` — событие идёт (тема
 * `winter`, баннер); `event: "finished"` — страница доступна, баннера и темы нет;
 * `eventArchive` — в профиле есть прошлое событие; `eventClaimConflict` — засчёт отвечает 409.
 * Состояние находок одно на мок-мир, сбрасывается `reset()`.
 */
import { http, HttpResponse, type HttpHandler } from "msw";
import type {
  EventArchiveCard,
  EventBannerCard,
  EventFindCard,
  EventPageView,
} from "../../src/shared/api/types";
import { csrfOk, problem, subjectOf } from "./handlers";
import type { MockWorld } from "./world";

const NO_STORE = { "Cache-Control": "private, no-store" };
export const EVENT_KEY = "winter";
const STARTS_AT = "2026-10-01T00:00:00Z";
const ENDS_AT = "2026-12-31T00:00:00Z";

type FindDef = Omit<EventFindCard, "state" | "found_at">;

const FINDS: FindDef[] = [
  {
    key: "snowman",
    number: 1,
    title: "Снеговик у магазина",
    x_pct: 18,
    y_pct: 62,
    final: false,
    diary: "Кто-то слепил снеговика прямо среди витрин.",
    reward: { xp: 50, verses: 5 },
    play_page: "shop",
    trigger: { type: "ui_spot", page_key: "shop" },
  },
  {
    key: "lantern",
    number: 2,
    title: "Фонарь на главной",
    x_pct: 46,
    y_pct: 30,
    final: false,
    diary: "Фонарь горит, хотя вокруг светло.",
    reward: { xp: 50, verses: 5 },
    play_page: "home",
    trigger: { type: "ui_spot", page_key: "home" },
  },
  {
    key: "duelist",
    number: 3,
    title: "Дуэлянт",
    x_pct: 72,
    y_pct: 55,
    final: false,
    diary: "Выиграйте три дуэли, чтобы он вышел из тени.",
    reward: { xp: 80, verses: 8 },
    play_page: "online",
    trigger: { type: "server", metric: "duel_win", progress: 1, target: 3, period: "event" },
  },
  {
    key: "star",
    number: 4,
    title: "Звезда",
    x_pct: 84,
    y_pct: 14,
    final: true,
    diary: "Она появится, когда будут найдены остальные.",
    reward: { xp: 200, verses: 20 },
    play_page: null,
    trigger: { type: "server" },
  },
];

const AFTER: Record<string, string> = {
  snowman: "Снеговик оказался заколдованным: в кармане лежала звезда-подсказка.",
  lantern: "Фонарь привёл к тайнику с вареньем.",
};

export class EventsWorld {
  /** Найденные находки: ключ → время. */
  found = new Map<string, string>();
  claimCalls = 0;
  claimKeys = new Map<string, EventFindCard>();

  reset(): void {
    this.found.clear();
    this.claimCalls = 0;
    this.claimKeys.clear();
    // Фонарь найден «ранее»: страница показывает и найденную, и открытую, и закрытую находки.
    this.found.set("lantern", "2026-11-02T10:00:00Z");
  }

  constructor() {
    this.reset();
  }
}

function card(world: MockWorld, def: FindDef): EventFindCard {
  const w = world.events;
  const foundAt =
    w.found.get(def.key) ?? (world.scenario.eventAllFound ? "2026-12-20T10:00:00Z" : null);
  const othersFound = FINDS.filter((f) => !f.final).every((f) => w.found.has(f.key));
  const state: EventFindCard["state"] = foundAt
    ? "found"
    : def.final && !othersFound
      ? "locked"
      : "open";
  const diary = foundAt ? (AFTER[def.key] ?? def.diary) : def.diary;
  // Закрытая финальная находка не раскрывает прогресс триггера.
  return { ...def, state, diary, found_at: foundAt };
}

function eventPage(world: MockWorld): EventPageView {
  const finds = FINDS.map((f) => card(world, f));
  const found = finds.filter((f) => f.state === "found").length;
  const finished = world.scenario.event === "finished";
  return {
    key: EVENT_KEY,
    title: "Зимняя охота",
    description: "Найдите все спрятанные вещи до конца зимы.",
    theme_key: "winter",
    theme: { key: "winter" },
    status: finished ? "finished" : "active",
    starts_at: STARTS_AT,
    ends_at: ENDS_AT,
    map_url: "/media/events/winter-map.svg",
    found,
    total: finds.length,
    completed: found === finds.length,
    reward_item: {
      item_key: "snow_frame",
      title: "Снежная рамка",
      image_url: "/media/events/snow-frame.svg",
    },
    finds,
  };
}

/** Блок `event` оболочки и главной; `null` — события нет. */
export function eventBanner(world: MockWorld, subject: string | null): EventBannerCard | null {
  if (world.scenario.event !== "active") return null;
  const page = eventPage(world);
  const user = Boolean(subject?.startsWith("user."));
  return {
    key: EVENT_KEY,
    title: page.title,
    ends_at: ENDS_AT,
    found: user ? page.found : null,
    total: user ? page.total : null,
  };
}

/** Ключ темы shell: тема активного события, иначе сценарий или `default`. */
export function themeKeyOf(world: MockWorld): string {
  return world.scenario.event === "active" ? "winter" : (world.scenario.themeKey ?? "default");
}

/** Архив событий профиля. */
export function eventsArchive(world: MockWorld): EventArchiveCard[] {
  const items: EventArchiveCard[] = [];
  if (world.scenario.event === "active" || world.scenario.event === "finished") {
    const page = eventPage(world);
    items.push({
      key: EVENT_KEY,
      title: page.title,
      status: page.status,
      starts_at: STARTS_AT,
      ends_at: ENDS_AT,
      found: page.found,
      total: page.total,
      completed: page.completed,
    });
  }
  if (world.scenario.eventArchive) {
    items.push({
      key: "autumn",
      title: "Осенний переполох",
      status: "finished",
      starts_at: "2025-10-01T00:00:00Z",
      ends_at: "2025-10-31T00:00:00Z",
      found: 5,
      total: 5,
      completed: true,
    });
  }
  return items;
}

/** Картинки события (карта и награда) - простые SVG; цвета здесь - данные мока, не токены UI. */
const eventSvg = (file: string) =>
  file.includes("map")
    ? `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300"><rect width="400" height="300" fill="#26405f"/></svg>`
    : `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="28" fill="#9fd4ff"/></svg>`;

export function createEventsHandlers(world: MockWorld): HttpHandler[] {
  return [
    http.get("*/media/events/:file", ({ params }) =>
      HttpResponse.text(eventSvg(String(params.file)), {
        headers: { "Content-Type": "image/svg+xml" },
      }),
    ),

    http.get("*/api/web/v1/pages/events/:key", ({ request, params }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.unauthenticated");
      if (!subject.startsWith("user.")) return problem(403, "auth.guest_forbidden");
      if (params.key !== EVENT_KEY || !world.scenario.event)
        return problem(404, "progression.event_not_found");
      return HttpResponse.json(eventPage(world), { headers: NO_STORE });
    }),

    http.post("*/api/progression/v1/events/:key/finds/:find/claim", ({ request, params }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.unauthenticated");
      if (!subject.startsWith("user.")) return problem(403, "auth.guest_forbidden");
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      const idem = request.headers.get("idempotency-key");
      if (!idem) return problem(422, "idempotency.key_required");
      const w = world.events;
      w.claimCalls += 1;
      if (params.key !== EVENT_KEY || !world.scenario.event)
        return problem(404, "progression.event_not_found");
      if (world.scenario.event !== "active" || world.scenario.eventClaimConflict)
        return problem(409, "progression.event_not_active");
      const def = FINDS.find((f) => f.key === params.find);
      if (!def) return problem(404, "progression.event_find_not_found");
      const body = (found: EventFindCard, newly: boolean) => {
        const page = eventPage(world);
        return {
          find: found,
          found: page.found,
          total: page.total,
          completed: page.completed,
          newly_unlocked: newly,
        };
      };
      const cached = w.claimKeys.get(idem);
      if (cached) return HttpResponse.json(body(card(world, def), false), { headers: NO_STORE });
      if (def.trigger.type !== "ui_spot") return problem(409, "progression.event_find_not_ui_spot");
      const current = card(world, def);
      if (current.state === "locked") return problem(409, "progression.event_find_locked");
      const newly = current.state === "open";
      if (newly) w.found.set(def.key, world.now().toISOString());
      const result = card(world, def);
      w.claimKeys.set(idem, result);
      return HttpResponse.json(body(result, newly), { headers: NO_STORE });
    }),
  ];
}
