/**
 * Полный стек: сезонное событие 5d (ADR 0054-0056).
 *
 * Один последовательный сценарий на засеянном стеке (`E2E_SEEDED=1`). Админ-контекст progression
 * (как в `shop.spec.ts`) создаёт предмет-рамку события (`event_key`, картинка, активный), событие
 * с тремя находками `ui_spot` (home, leaderboards, финал на profile; тема `winter`), запуск «от
 * сейчас» и публикует его. Игрок регистрируется через UI, две находки открывает кликом по
 * спрятанному элементу (`event-spot` -> claim), видит «Найдено 2 из 3» на странице события и
 * уведомление `find_unlocked`; финал закрывает через API (тот же claim), после чего рамка события
 * лежит в инвентаре, а событие помечено пройденным. Если на стенде уже есть опубликованный запуск
 * с пересекающимся периодом (`event_period_overlap`), сценарий пропускается.
 *
 * Доставка асинхронная (outbox -> брокер -> consumer, кэш баннера bff ≤ 30 с): ожидания
 * состояний с таймаутом ≤ 90 с, пауз нет.
 */
import { randomUUID } from "node:crypto";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import {
  ADMIN,
  HAS_ADMIN,
  NO_ADMIN_REASON,
  adminContext,
  makePng,
} from "./admin";
import { api, apiErrorsOf, closePlayers, json, registerPlayer, RUN, type Player } from "./online";

test.skip(process.env.E2E_SEEDED !== "1", "стек не засеян: нужен шаг засева (E2E_SEEDED=1)");
test.skip(!HAS_ADMIN, NO_ADMIN_REASON);
test.describe.configure({ mode: "serial", timeout: 240_000 });

const DELIVERY_TIMEOUT_MS = 90_000;
const SEED_FRAME = "frm_neon"; // сидовая рамка: берём у неё слот и редкость (seeds/progression/items)
const EVENT_KEY = `e2e_${RUN}`;
const ITEM_KEY = `e2e_frame_${RUN}`;
const FINDS = {
  first: { key: "first_spot", page: "home" },
  second: { key: "second_spot", page: "leaderboards" },
  final: { key: "final_spot", page: "profile" },
} as const;

type AdminItem = { id: string; key: string; slot: string; rarity: string };
type Notice = { id: string; type: string; title: string; body: string; data?: Record<string, string> };

let player: Player;
let admin: APIRequestContext;
let skipReason = "";

const headers = () => ({ "Idempotency-Key": randomUUID() });

test.beforeAll(async ({ browser }) => {
  test.setTimeout(180_000);
  admin = await adminContext(ADMIN.progression);

  // Предмет события: слот и редкость — как у сидовой рамки; картинка и активность — до публикации.
  const items = await json<{ items: AdminItem[] }>(await admin.get("/admin/v1/items?limit=100"));
  const seed = items.items.find((i) => i.key === SEED_FRAME);
  expect(seed, `сидовый предмет ${SEED_FRAME}`).toBeTruthy();
  const base = {
    key: ITEM_KEY,
    slot: seed!.slot,
    rarity: seed!.rarity,
    title: `Рамка события ${RUN}`,
    media_kind: "static",
    price: null,
    level_required: null,
    event_key: EVENT_KEY,
  };
  const item = await json<AdminItem>(
    await admin.post("/admin/v1/items", {
      headers: headers(),
      data: { ...base, active: false, reason: `e2e: предмет события ${RUN}` },
    }),
    201,
  );
  await json(
    await admin.put(`/admin/v1/items/${item.id}/media`, {
      headers: headers(),
      multipart: {
        reason: `e2e: картинка ${RUN}`,
        file: { name: "frame.png", mimeType: "image/png", buffer: makePng(96) },
      },
    }),
  );
  await json(
    await admin.put(`/admin/v1/items/${item.id}`, {
      headers: headers(),
      data: { ...base, active: true, reason: `e2e: активация ${RUN}` },
    }),
  );

  const find = (f: { key: string; page: string }, number: number, final: boolean) => ({
    key: f.key,
    number,
    title: `Находка ${number}`,
    diary_before: "Где-то спрятан элемент.",
    diary_after: "Элемент найден.",
    x_pct: 20 * number,
    y_pct: 30 + 10 * number,
    reward: { xp: 10, verses: 1 },
    final,
    play_page: f.page,
    trigger: { type: "ui_spot", page_key: f.page },
  });
  await json(
    await admin.post("/admin/v1/events", {
      headers: headers(),
      data: {
        key: EVENT_KEY,
        reason: `e2e: событие ${RUN}`,
        body: {
          title: `Зимнее событие ${RUN}`,
          description: "Сквозной сценарий e2e: три спрятанных элемента.",
          theme_key: "winter",
          finds: [
            find(FINDS.first, 1, false),
            find(FINDS.second, 2, false),
            find(FINDS.final, 3, true),
          ],
          reward: { item_key: ITEM_KEY },
        },
      },
    }),
    201,
  );

  // Запуск «от сейчас» на два часа; публикация привязана к хешу определения.
  const now = Date.now();
  const run = await json<{ id: string }>(
    await admin.post(`/admin/v1/events/${EVENT_KEY}/runs`, {
      headers: headers(),
      data: {
        starts_at: new Date(now - 20_000).toISOString(),
        ends_at: new Date(now + 2 * 3600_000).toISOString(),
        reason: `e2e: запуск ${RUN}`,
      },
    }),
    201,
  );
  const event = await json<{ body_sha256: string }>(await admin.get(`/admin/v1/events/${EVENT_KEY}`));
  const published = await admin.post(`/admin/v1/events/${EVENT_KEY}/runs/${run.id}/publish`, {
    headers: headers(),
    data: { body_sha256: event.body_sha256, reason: `e2e: публикация ${RUN}` },
  });
  if (published.status() === 409 && (await published.text()).includes("event_period_overlap")) {
    skipReason = "на стенде уже есть опубликованный запуск с пересекающимся периодом";
    return;
  }
  await json(published);

  player = await registerPlayer(browser, "evt");
});

test.afterAll(async () => {
  // Остановить запуск, чтобы событие не мешало следующим прогонам (best effort).
  try {
    const event = await json<{ runs: { id: string; status: string }[] }>(
      await admin.get(`/admin/v1/events/${EVENT_KEY}`),
    );
    for (const run of event.runs.filter((r) => r.status === "published" || r.status === "active"))
      await admin.post(`/admin/v1/events/${EVENT_KEY}/runs/${run.id}/stop`, {
        headers: headers(),
        data: { reason: `e2e: уборка ${RUN}` },
      });
  } catch {
    // уборка не должна ронять прогон
  }
  await admin?.dispose();
  await closePlayers(player ? [player] : []);
});

test.beforeEach(() => {
  test.skip(skipReason !== "", skipReason);
});

test.afterEach(async ({}, testInfo) => {
  if (player && testInfo.status !== testInfo.expectedStatus)
    await testInfo.attach("api-errors", { body: apiErrorsOf([player.page]) });
});

// ----------------------------------------------------------------------------- данные
const noticesOf = async (page: Page): Promise<Notice[]> =>
  (await json<{ items: Notice[] }>(await api(page, "notifications").get("/notifications?limit=50")))
    .items;

/**
 * Дождаться активного события в оболочке: баннер bff кэшируется ≤ 30 с. Опрашиваем JSON
 * `/pages/shell` (редко, без перезагрузки страницы: каждая загрузка тянет realtime-токен и упирается
 * в лимит identity), страницу открываем один раз после этого.
 */
async function waitForActiveEvent(page: Page, what: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const res = await page.request.get("/api/web/v1/pages/shell");
        if (!res.ok()) return false; // 429/5xx — ждём следующего опроса
        return ((await res.json()) as { event: unknown }).event !== null;
      },
      { timeout: DELIVERY_TIMEOUT_MS, intervals: [3_000], message: what },
    )
    .toBe(true);
}

/** Найти спрятанный элемент на странице: событие ждём по JSON оболочки, страницу грузим один раз. */
async function clickSpot(page: Page, path: string, findKey: string): Promise<void> {
  await waitForActiveEvent(page, `активное событие перед ${path}`);
  await page.goto(path);
  await expect(page.getByTestId("event-spot")).toBeVisible({ timeout: 30_000 });
  const spot = page.getByTestId("event-spot");
  await expect(spot).toHaveAttribute("data-find", findKey);
  const [res] = await Promise.all([
    page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        new URL(r.url()).pathname ===
          `/api/progression/v1/events/${EVENT_KEY}/finds/${findKey}/claim`,
    ),
    spot.click(),
  ]);
  expect(res.status(), await res.text()).toBe(200);
}

// ----------------------------------------------------------------------------- сценарий
test("активное событие: баннер на главной, страница события, тема winter", async () => {
  const { page } = player;
  await waitForActiveEvent(page, "баннер активного события на главной");
  await page.goto("/");
  await expect(page.getByTestId("home-event")).toBeVisible({ timeout: 30_000 });
  await page.goto(`/events/${EVENT_KEY}`);
  await expect(page.getByTestId("event-page")).toBeVisible();
  await expect(page.getByTestId("event-progress")).toHaveText("Найдено 0 из 3");
  await expect(page.getByTestId(`event-point-${FINDS.first.key}`)).toHaveAttribute(
    "data-state",
    "open",
  );
  await expect(page.getByTestId(`event-point-${FINDS.final.key}`)).toHaveAttribute(
    "data-state",
    "locked",
  );
});

test("две находки кликом по элементу: прогресс «2 из 3» и уведомление find_unlocked", async () => {
  const { page } = player;
  await clickSpot(page, "/", FINDS.first.key);
  await clickSpot(page, "/leaderboards", FINDS.second.key);

  await page.goto(`/events/${EVENT_KEY}`);
  await expect(page.getByTestId("event-progress")).toHaveText("Найдено 2 из 3");
  for (const f of [FINDS.first, FINDS.second])
    await expect(page.getByTestId(`event-point-${f.key}`)).toHaveAttribute("data-state", "found");
  await expect(page.getByTestId(`event-point-${FINDS.final.key}`)).toHaveAttribute(
    "data-state",
    "open",
  );

  // Уведомление по второй находке: «найдено 2 из 3», ссылка на событие в data.
  await expect
    .poll(
      async () =>
        (await noticesOf(page)).filter(
          (n) => n.type === "find_unlocked" && n.body.includes("найдено 2 из 3"),
        ).length,
      { timeout: DELIVERY_TIMEOUT_MS, message: "уведомление find_unlocked (2 из 3)" },
    )
    .toBe(1);
  const notice = (await noticesOf(page)).find((n) => n.body.includes("найдено 2 из 3"))!;
  expect(notice.data?.event_key).toBe(EVENT_KEY);
});

test("финал: событие пройдено, рамка события в инвентаре, уведомление event_completed", async () => {
  const { page } = player;
  const claim = await json<{ found: number; total: number; completed: boolean }>(
    await api(page, "progression").post(`/events/${EVENT_KEY}/finds/${FINDS.final.key}/claim`),
  );
  expect([claim.found, claim.total, claim.completed]).toEqual([3, 3, true]);

  await page.goto(`/events/${EVENT_KEY}`);
  await expect(page.getByTestId("event-progress")).toHaveText("Найдено 3 из 3");
  await expect(page.getByTestId("event-reward")).toHaveAttribute("data-completed", "true");

  // Основная награда — рамка события — выдаётся асинхронно (журнал наград, ADR 0056).
  await expect
    .poll(
      async () => {
        await page.goto("/shop");
        await page.getByTestId("shop-tab-inventory").click();
        return page.getByTestId(`inventory-item-${ITEM_KEY}`).count();
      },
      { timeout: DELIVERY_TIMEOUT_MS, message: "рамка события в инвентаре" },
    )
    .toBeGreaterThan(0);

  await expect
    .poll(async () => (await noticesOf(page)).filter((n) => n.type === "event_completed").length, {
      timeout: DELIVERY_TIMEOUT_MS,
      message: "уведомление event_completed",
    })
    .toBe(1);
  const done = (await noticesOf(page)).find((n) => n.type === "event_completed")!;
  expect(done.body).toContain(`Зимнее событие ${RUN}`);
});
