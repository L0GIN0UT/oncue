/**
 * Мок этапа 5b по контрактам `contracts/openapi/{progression,bff_web}.json`: магазин
 * (`/pages/shop`, покупка), инвентарь и оформление (блоки профиля, `PUT me/appearance/{slot}`),
 * промокод, своя аватарка и shell (`/pages/shell`: тема и колокольчик). Уведомления — `notifications.ts`.
 *
 * Сценарии (`POST /__mock/scenario`, поля `Scenario`): `shopDown` — витрина недоступна;
 * `shellDown` — shell отвечает 503; `themeKey` — ключ темы shell; `balance` — стартовые Версы;
 * `promoRateLimited` — погашение 429 с `Retry-After: 3`; `avatarFail` — `too_large | invalid |
 * rate`; `avatarRequest` — предустановленная заявка (`pending | approved | rejected`);
 * `appearanceDown` — блоки `appearance`/`inventory`/`avatar` профиля недоступны.
 * Состояние одно на мок-мир (e2e идёт одним игроком), сбрасывается `reset()`.
 */
import { http, HttpResponse, type HttpHandler } from "msw";
import type {
  AppearanceCard,
  AvatarCard,
  CosmeticCard,
  InventoryCard,
  ShopItemState,
  ShopPage,
} from "../../src/shared/api/types";
import { csrfOk, problem, subjectOf } from "./handlers";
import type { MockWorld } from "./world";

const NO_STORE = { "Cache-Control": "private, no-store" };
const LEVEL = 7;
const START_BALANCE = 40;

let seq = 0;
const cosmetic = (
  key: string,
  slot: string,
  title: string,
  rarity: string,
  extra: Partial<CosmeticCard> = {},
): CosmeticCard => ({
  id: `0192a000-0000-7000-9000-${String(++seq).padStart(12, "0")}`,
  key,
  slot,
  title,
  rarity,
  media_kind: "static",
  asset_url: `/cosmetics/${key}.svg`,
  poster_url: null,
  ...extra,
});

/** Порядок слотов — из данных витрины (конфиг progression `shop.slots`). */
export const SLOTS = ["avatar", "frame", "title", "background"];

export const ITEMS: CosmeticCard[] = [
  cosmetic("fox", "avatar", "Лис", "common", { price: 20 }),
  cosmetic("owl", "avatar", "Сова", "rare", {
    price: 35,
    media_kind: "animated",
    poster_url: "/cosmetics/owl_poster.svg",
  }),
  cosmetic("custom_avatar", "avatar", "Своя аватарка", "epic", { price: 50 }),
  cosmetic("gold_frame", "frame", "Золотая рамка", "rare", { price: 30, level_required: 5 }),
  cosmetic("neon_frame", "frame", "Неоновая рамка", "epic", {
    price: 100,
    level_required: 20,
    media_kind: "animated",
    poster_url: "/cosmetics/neon_frame_poster.svg",
  }),
  cosmetic("champion", "title", "Чемпион", "rare", { price: 10 }),
  cosmetic("stars", "background", "Звёздное небо", "common", { price: 15 }),
];

export const itemByKey = (key: string) => ITEMS.find((i) => i.key === key);
export const itemById = (id: string) => ITEMS.find((i) => i.id === id);

/** SVG-заглушка предмета (мок вместо `/cosmetics/*.webp`). */
export function cosmeticSvg(name: string): string {
  const hue = ([...name].reduce((s, c) => s + c.charCodeAt(0), 0) * 37) % 360;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64"><circle cx="32" cy="32" r="28" fill="hsl(${hue} 60% 45%)"/></svg>`;
}

type Source = "purchase" | "promo" | "wheel";

export class EconomyWorld {
  balance = START_BALANCE;
  owned = new Map<string, Source>();
  /** слот → id надетого предмета */
  equipped = new Map<string, string>();
  purchaseCalls = 0;
  /** Ключи идемпотентности покупок: ключ → ответ (повтор — та же выдача). */
  purchaseKeys = new Map<string, string>();
  promoRedeemed = new Map<string, number>();
  avatarSubmits = 0;
  avatarRequest: AvatarCard["request"] = null;
  customAvatarUrl: string | null = null;
  lastIdempotencyKeys: string[] = [];

  reset(): void {
    this.balance = START_BALANCE;
    this.owned.clear();
    this.equipped.clear();
    this.purchaseCalls = 0;
    this.purchaseKeys.clear();
    this.promoRedeemed.clear();
    this.avatarSubmits = 0;
    this.avatarRequest = null;
    this.customAvatarUrl = null;
    this.lastIdempotencyKeys = [];
  }
}

function stateOf(world: MockWorld, item: CosmeticCard): ShopItemState {
  if (world.economy.owned.has(item.id)) return "owned";
  return item.level_required != null && item.level_required > LEVEL ? "locked" : "available";
}

const down = (world: MockWorld, name: string) => world.scenario.progressDown?.includes(name);
const unavailable = { status: "unavailable" as const, data: null };

export function shopPage(world: MockWorld): ShopPage {
  const eco = world.economy;
  return {
    blocks: {
      catalog: world.scenario.shopDown
        ? unavailable
        : {
            status: "ok",
            data: {
              balance: eco.balance,
              slots: SLOTS,
              items: ITEMS.map((item) => ({ item, state: stateOf(world, item) })),
            },
          },
      packs: null,
    },
  };
}

export function appearanceCard(world: MockWorld): AppearanceCard {
  const eco = world.economy;
  return {
    custom_avatar_url: eco.customAvatarUrl,
    slots: SLOTS.map((slot) => {
      const id = eco.equipped.get(slot);
      return { slot, item: (id && itemById(id)) || null };
    }),
  };
}

function inventoryCard(world: MockWorld): InventoryCard {
  const eco = world.economy;
  return {
    items: [...eco.owned].flatMap(([id, source]) => {
      const item = itemById(id);
      return item
        ? [{ item, source, equipped: eco.equipped.get(item.slot) === id, expires_at: null }]
        : [];
    }),
  };
}

export function avatarCard(world: MockWorld): AvatarCard {
  const eco = world.economy;
  const custom = itemByKey("custom_avatar")!;
  const preset = world.scenario.avatarRequest;
  const request =
    eco.avatarRequest ??
    (preset
      ? {
          status: preset,
          submitted_at: "2026-10-23T10:00:00Z",
          reason_code: preset === "rejected" ? "inappropriate" : null,
        }
      : null);
  return {
    entitled: eco.owned.has(custom.id) || Boolean(preset),
    formats: ["jpeg", "png", "webp"],
    max_bytes: 1_000_000,
    request,
  };
}

/** Блоки профиля 5b: `appearance`, `inventory`, `avatar` (гаснут сценарием `appearanceDown`). */
export function economyProfileBlocks(world: MockWorld) {
  const off = world.scenario.appearanceDown || down(world, "economy");
  return {
    appearance: off ? unavailable : { status: "ok" as const, data: appearanceCard(world) },
    inventory: off ? unavailable : { status: "ok" as const, data: inventoryCard(world) },
    avatar: off ? unavailable : { status: "ok" as const, data: avatarCard(world) },
  };
}

const userOnly = (request: Request): Response | string => {
  const subject = subjectOf(request);
  if (!subject) return problem(401, "auth.unauthenticated");
  if (!subject.startsWith("user.")) return problem(403, "auth.guest_forbidden");
  return subject;
};

const SECOND_PLAYER = "00000000-0000-4000-8000-000000000902";

export function createEconomyHandlers(world: MockWorld): HttpHandler[] {
  const eco = world.economy;
  const itemView = (item: CosmeticCard) => ({
    ...item,
    level_required: item.level_required ?? null,
    price: item.price ?? null,
    event_key: null,
  });

  return [
    http.get("*/api/web/v1/pages/shell", ({ request }) => {
      if (world.scenario.shellDown) return problem(503, "bff.unavailable");
      const subject = subjectOf(request);
      const bell = subject?.startsWith("user.")
        ? { status: "ok" as const, data: { unread: world.notifications.unread() } }
        : null;
      return HttpResponse.json(
        { theme: { key: world.scenario.themeKey ?? "default" }, bell },
        { headers: subject ? NO_STORE : { "Cache-Control": "public, max-age=30" } },
      );
    }),

    http.get("*/api/web/v1/pages/shop", ({ request }) => {
      const who = userOnly(request);
      if (who instanceof Response) return who;
      if (world.scenario.balance !== undefined && eco.purchaseCalls === 0)
        eco.balance = world.scenario.balance;
      return HttpResponse.json(shopPage(world), { headers: NO_STORE });
    }),

    http.get("*/cosmetics/:file", ({ params }) =>
      HttpResponse.text(cosmeticSvg(String(params.file)), {
        headers: { "Content-Type": "image/svg+xml" },
      }),
    ),

    http.post("*/api/progression/v1/shop/items/:id/purchase", ({ request, params }) => {
      const who = userOnly(request);
      if (who instanceof Response) return who;
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      const key = request.headers.get("idempotency-key");
      if (!key) return problem(422, "validation.failed");
      eco.lastIdempotencyKeys.push(key);
      const item = itemById(String(params.id));
      if (!item) return problem(404, "progression.item_not_found");
      const done = eco.purchaseKeys.get(key);
      if (done)
        return HttpResponse.json(
          { item: itemView(item), balance_after: eco.balance, repeated: true },
          { headers: NO_STORE },
        );
      if (eco.owned.has(item.id)) return problem(409, "progression.already_owned");
      if (stateOf(world, item) === "locked") return problem(403, "progression.item_not_available");
      if ((item.price ?? 0) > eco.balance) return problem(409, "progression.insufficient_funds");
      eco.purchaseCalls += 1;
      eco.balance -= item.price ?? 0;
      eco.owned.set(item.id, "purchase");
      eco.purchaseKeys.set(key, item.id);
      return HttpResponse.json(
        { item: itemView(item), balance_after: eco.balance, repeated: false },
        { headers: NO_STORE },
      );
    }),

    http.put("*/api/progression/v1/me/appearance/:slot", async ({ request, params }) => {
      const who = userOnly(request);
      if (who instanceof Response) return who;
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      const slot = String(params.slot);
      if (!SLOTS.includes(slot)) return problem(404, "progression.slot_not_found");
      const { item_id } = (await request.json()) as { item_id: string | null };
      if (item_id === null) eco.equipped.delete(slot);
      else {
        const item = itemById(item_id);
        if (!item || !eco.owned.has(item_id)) return problem(404, "progression.item_not_owned");
        if (item.slot !== slot) return problem(422, "progression.item_slot_mismatch");
        eco.equipped.set(slot, item_id);
      }
      const card = appearanceCard(world);
      return HttpResponse.json(
        {
          slots: card.slots.map((s) => ({ slot: s.slot, item: s.item ? itemView(s.item) : null })),
        },
        { headers: NO_STORE },
      );
    }),

    http.get("*/api/progression/v1/appearance", ({ request }) => {
      const who = userOnly(request);
      if (who instanceof Response) return who;
      const ids = new URL(request.url).searchParams.getAll("user_ids");
      const mine = who.replace(/^user\./, "");
      const card = appearanceCard(world);
      const players = ids
        .filter((id) => id === mine || id === SECOND_PLAYER)
        .map((id) => ({
          user_id: id,
          custom_avatar_url: id === mine ? card.custom_avatar_url : null,
          appearance: {
            slots:
              id === mine
                ? card.slots.map((s) => ({
                    slot: s.slot,
                    item: s.item ? itemView(s.item) : null,
                  }))
                : [],
          },
        }));
      return HttpResponse.json({ players }, { headers: NO_STORE });
    }),

    http.post("*/api/progression/v1/promo/redeem", async ({ request }) => {
      const who = userOnly(request);
      if (who instanceof Response) return who;
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      if (!request.headers.get("idempotency-key")) return problem(422, "validation.failed");
      if (world.scenario.promoRateLimited) {
        const res = problem(429, "progression.promo_rate_limited");
        res.headers.set("Retry-After", "3");
        return res;
      }
      const { code } = (await request.json()) as { code: string };
      const norm = code.trim().toUpperCase();
      const repeated = eco.promoRedeemed.has(norm);
      if (norm === "WELCOME") {
        if (!repeated) {
          eco.balance += 25;
          eco.promoRedeemed.set(norm, 25);
        }
        return HttpResponse.json(
          { reward_kind: "verses", amount: 25, item: null, repeated },
          { headers: NO_STORE },
        );
      }
      if (norm === "FOXY") {
        const fox = itemByKey("fox")!;
        eco.owned.set(fox.id, "promo");
        eco.promoRedeemed.set(norm, 0);
        return HttpResponse.json(
          { reward_kind: "item", amount: null, item: itemView(fox), repeated },
          { headers: NO_STORE },
        );
      }
      return problem(404, "progression.promo_invalid");
    }),

    http.post("*/api/progression/v1/me/avatar", async ({ request }) => {
      const who = userOnly(request);
      if (who instanceof Response) return who;
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      if (!request.headers.get("idempotency-key")) return problem(422, "validation.failed");
      const fail = world.scenario.avatarFail;
      if (fail === "too_large") return problem(413, "ugc.too_large");
      if (fail === "invalid") return problem(422, "ugc.invalid_image");
      if (fail === "rate") {
        const res = problem(429, "rate_limited");
        res.headers.set("Retry-After", "30");
        return res;
      }
      const form = await request.formData();
      if (!(form.get("file") instanceof Blob)) return problem(422, "validation.failed");
      eco.avatarSubmits += 1;
      eco.avatarRequest = {
        status: "pending",
        submitted_at: world.now().toISOString(),
        reason_code: null,
      };
      return HttpResponse.json(
        {
          asset_id: "0192a000-0000-7000-9000-aaaaaaaaaaaa",
          status: "pending",
          reason_code: null,
          submitted_at: eco.avatarRequest.submitted_at,
        },
        { status: 202, headers: NO_STORE },
      );
    }),
  ];
}
