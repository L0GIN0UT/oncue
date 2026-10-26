/**
 * Полный стек: своя аватарка -> модерация -> уведомления и настройки 5b (ADR 0047-0048).
 *
 * Один последовательный сценарий на засеянном стеке (`gv dev seed`): игрок регистрируется
 * через UI, админ-контекст начисляет Версы на товар `custom_avatar` (цена из данных витрины),
 * игрок покупает его в `/shop` и грузит маленький PNG (генерируется в тесте) в `/profile`.
 * Заявка попадает в очередь media-pipeline (`/admin/v1/ugc-images?status=pending`, ищется по
 * `owner_user_id`), админ одобряет её -> `AssetModerated` -> progression (аватарка) и
 * notifications (уведомление «Аватарка одобрена», колокольчик). Вторая заявка отклоняется с
 * `reason_code = inappropriate` (текст — из `notification_types.yaml`). Дальше настройки типов
 * (`user_mutable = false` у avatar_*) и страница `/unsubscribe` с недействительным токеном
 * (полный сценарий отписки нужен токен из письма рассылки — здесь не проверяется).
 *
 * Доставка асинхронная (outbox -> брокер -> consumer; realtime или опрос счётчика раз в
 * ~60 с): везде ожидания состояний с таймаутом ≤ 90 с, пауз нет.
 */
import { randomUUID } from "node:crypto";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import {
  ADMIN,
  HAS_ADMIN,
  NO_ADMIN_REASON,
  adminContext,
  grantVerses,
  makePng,
  userIdOf,
} from "./admin";
import { api, apiErrorsOf, closePlayers, json, registerPlayer, RUN, type Player } from "./online";

test.skip(process.env.E2E_SEEDED !== "1", "стек не засеян: нужен шаг засева (E2E_SEEDED=1)");
test.skip(!HAS_ADMIN, NO_ADMIN_REASON);
test.describe.configure({ mode: "serial", timeout: 240_000 });

const CUSTOM_AVATAR = "custom_avatar"; // seeds/progression/items/items.yaml (price 500, slot avatar)
const DELIVERY_TIMEOUT_MS = 90_000;
const MOD_TIMEOUT_MS = 60_000;

type UgcImage = { asset_id: string; owner_user_id: string; purpose: string; status: string };
type Notice = { id: string; type: string; title: string; body: string; read_at: string | null };
type MyAvatar = {
  entitled: boolean;
  current_url: string | null;
  request: { status: string; reason_code?: string | null } | null;
};

let player: Player;
let progression: APIRequestContext;
let media: APIRequestContext;
let userId = "";

test.beforeAll(async ({ browser }) => {
  test.setTimeout(180_000);
  player = await registerPlayer(browser, "ntf");
  progression = await adminContext(ADMIN.progression);
  media = await adminContext(ADMIN.media);
  userId = await userIdOf(player.page);
});

test.afterAll(async () => {
  await progression?.dispose();
  await media?.dispose();
  await closePlayers(player ? [player] : []);
});

test.afterEach(async ({}, testInfo) => {
  if (player && testInfo.status !== testInfo.expectedStatus)
    await testInfo.attach("api-errors", { body: apiErrorsOf([player.page]) });
});

// ----------------------------------------------------------------------------- помощники
/** Первая заявка очереди `pending` (purpose avatar) этого игрока или `null`; страницы по курсору. */
async function pendingOf(owner: string): Promise<UgcImage | null> {
  let cursor: string | null = null;
  do {
    const query: string = `status=pending&purpose=avatar&limit=100${cursor ? `&cursor=${cursor}` : ""}`;
    const page = await json<{ items: UgcImage[]; next_cursor: string | null }>(
      await media.get(`/admin/v1/ugc-images?${query}`),
    );
    const hit = page.items.find((i) => i.owner_user_id === owner);
    if (hit) return hit;
    cursor = page.next_cursor;
  } while (cursor);
  return null;
}

async function waitPending(owner: string): Promise<UgcImage> {
  let found: UgcImage | null = null;
  await expect
    .poll(async () => (found = await pendingOf(owner)) !== null, {
      timeout: MOD_TIMEOUT_MS,
      message: "заявка на аватарку в очереди модерации media-pipeline",
    })
    .toBe(true);
  return found!;
}

const noticesOf = async (page: Page): Promise<Notice[]> =>
  (await json<{ items: Notice[] }>(await api(page, "notifications").get("/notifications?limit=50")))
    .items;

/** Загрузить PNG в `/profile` и отправить на проверку (ждём ответ 202). */
async function uploadAvatar(page: Page, seed: number): Promise<void> {
  await page.goto("/profile");
  await expect(page.getByTestId("block-avatar")).toBeVisible();
  await page.getByTestId("avatar-upload").setInputFiles({
    name: `me${seed}.png`,
    mimeType: "image/png",
    buffer: makePng(160, seed),
  });
  await expect(page.getByRole("img", { name: "Предпросмотр" })).toBeVisible();
  const [res] = await Promise.all([
    page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        new URL(r.url()).pathname === "/api/progression/v1/me/avatar",
    ),
    page.getByRole("button", { name: "Отправить на проверку" }).click(),
  ]);
  expect(res.status(), await res.text()).toBe(202);
  await expect(page.getByTestId("avatar-sent")).toBeVisible();
}

// ----------------------------------------------------------------------------- сценарий
test("покупка товара «своя аватарка» и загрузка PNG: заявка на проверке", async () => {
  const { page } = player;
  const shop = await json<{
    blocks: {
      catalog: { data: { items: { item: { id: string; key: string; slot: string; price: number | null }; state: string }[] } | null };
    };
  }>(await page.request.get("/api/web/v1/pages/shop"));
  const entry = shop.blocks.catalog.data?.items.find((e) => e.item.key === CUSTOM_AVATAR);
  expect(entry, `товар сида ${CUSTOM_AVATAR} в витрине`).toBeTruthy();
  const { item } = entry!;
  expect(item.price, "цена custom_avatar").toBeGreaterThan(0);
  await grantVerses(progression, userId, item.price!, `e2e: уведомления ${RUN}`);

  // Без права загрузки нет: профиль ведёт в магазин.
  await page.goto("/profile");
  await expect(page.getByTestId("avatar-status")).toHaveAttribute("data-status", "none");
  await expect(page.getByTestId("avatar-upload")).toHaveCount(0);

  await page.goto("/shop");
  await page.getByTestId(`shop-tab-${item.slot}`).click();
  await page.getByTestId(`shop-item-${CUSTOM_AVATAR}`).click();
  await page.getByTestId(`shop-buy-${CUSTOM_AVATAR}`).click();
  await page.getByTestId("shop-confirm").click();
  await expect(page.getByTestId(`shop-item-${CUSTOM_AVATAR}`)).toHaveAttribute("data-state", "owned");

  await uploadAvatar(page, 1);
  await page.reload();
  await expect(page.getByTestId("avatar-status")).toHaveAttribute("data-status", "pending");
});

test("админ одобряет заявку: колокольчик, уведомление, «Прочитать все»", async () => {
  const { page } = player;
  const pending = await waitPending(userId);
  expect(pending.purpose).toBe("avatar");
  const approved = await media.post(`/admin/v1/ugc-images/${pending.asset_id}/approve`, {
    headers: { "Idempotency-Key": randomUUID() },
    data: { reason: `e2e: одобрено ${RUN}` },
  });
  expect(approved.status(), await approved.text()).toBe(200);

  // Колокольчик: realtime или опрос счётчика (раз в ~60 с).
  await page.goto("/profile");
  const count = page.getByTestId("notification-count");
  await expect(count).toBeVisible({ timeout: DELIVERY_TIMEOUT_MS });
  expect(Number(await count.getAttribute("data-count"))).toBeGreaterThanOrEqual(1);

  await page.getByTestId("notification-bell").click();
  await expect(page.getByTestId("notifications-panel")).toBeVisible();
  await expect
    .poll(async () => (await noticesOf(page)).some((t) => t.type === "avatar_approved"), {
      timeout: DELIVERY_TIMEOUT_MS,
      message: "уведомление avatar_approved",
    })
    .toBe(true);
  await page.goto("/notifications");
  const item = page.locator("[data-testid=notification-item][data-type=avatar_approved]");
  await expect(item).toHaveCount(1);
  await expect(item).toContainText("Аватарка одобрена");
  await expect(item).toHaveAttribute("data-read", "false");

  await page.getByTestId("notifications-read-all").click();
  await expect(page.getByTestId("notification-count")).toHaveCount(0);
  await expect(page.locator("[data-testid=notification-item][data-read=false]")).toHaveCount(0);
  await expect(page.getByTestId("notifications-read-all")).toBeDisabled();
  const unread = await json<{ count: number }>(
    await api(page, "notifications").get("/notifications/unread-count"),
  );
  expect(unread.count).toBe(0);
});

test("после одобрения аватарка игрока — /ugc/…webp, статус «одобрена»", async () => {
  const { page } = player;
  let url: string | null = null;
  await expect
    .poll(
      async () => {
        const mine = await json<MyAvatar>(await api(page, "progression").get("/me/avatar"));
        url = mine.current_url;
        return `${mine.request?.status}:${url ? "url" : "-"}`;
      },
      { timeout: MOD_TIMEOUT_MS, message: "progression применил решение модератора" },
    )
    .toBe("approved:url");
  expect(url).toMatch(/^\/ugc\/.+\.webp$/);

  await page.goto("/profile");
  await expect(page.getByTestId("avatar-status")).toHaveAttribute("data-status", "approved");
  const img = page.getByTestId("player-badge").first().locator(`img[src="${url}"]`);
  await expect(img).toBeVisible();
  const file = await page.request.get(url!);
  expect(file.status(), url!).toBe(200);
});

test("отклонение второй заявки с причиной: уведомление и статус", async () => {
  const { page } = player;
  await uploadAvatar(page, 2);
  const pending = await waitPending(userId);
  const rejected = await media.post(`/admin/v1/ugc-images/${pending.asset_id}/reject`, {
    headers: { "Idempotency-Key": randomUUID() },
    data: { reason_code: "inappropriate", reason: `e2e: отклонено ${RUN}` },
  });
  expect(rejected.status(), await rejected.text()).toBe(200);

  // Уведомление приходит асинхронно: ждём его в API, затем проверяем, что видит игрок.
  await expect
    .poll(async () => (await noticesOf(page)).some((n) => n.type === "avatar_rejected"), {
      timeout: DELIVERY_TIMEOUT_MS,
      message: "уведомление avatar_rejected",
    })
    .toBe(true);
  await page.goto("/notifications");
  const item = page.locator("[data-testid=notification-item][data-type=avatar_rejected]");
  await expect(item).toHaveCount(1);
  await expect(item).toContainText("Аватарка отклонена");
  await expect(item).toContainText("нарушает правила сообщества");

  await page.goto("/profile");
  const status = page.getByTestId("avatar-status");
  await expect(status).toHaveAttribute("data-status", "rejected");
  await expect(status).toContainText("Неприемлемое содержание");
});

test("настройки: avatar_* отключить нельзя, тип level_up переключается и возвращается", async () => {
  const { page } = player;
  await page.goto("/notifications/settings");
  for (const type of ["avatar_approved", "avatar_rejected"]) {
    const row = page.getByTestId(`notification-type-${type}`);
    await expect(row).toBeVisible();
    for (const box of await row.getByRole("checkbox").all()) await expect(box).toBeDisabled();
    await expect(row).toContainText("нельзя");
  }

  const box = page
    .getByTestId("notification-type-level_up")
    .getByRole("checkbox", { name: "Новый уровень — В приложении" });
  await expect(box).toBeEnabled();
  const initial = await box.isChecked();

  const flip = async (value: boolean) => {
    const [res] = await Promise.all([
      page.waitForResponse(
        (r) =>
          r.request().method() === "PUT" &&
          new URL(r.url()).pathname === "/api/notifications/v1/notification-settings/level_up",
      ),
      value ? box.check() : box.uncheck(),
    ]);
    expect(res.status(), await res.text()).toBe(200);
    await page.reload();
    const again = page
      .getByTestId("notification-type-level_up")
      .getByRole("checkbox", { name: "Новый уровень — В приложении" });
    await expect(again).toBeEnabled();
    if (value) await expect(again).toBeChecked();
    else await expect(again).not.toBeChecked();
  };
  await flip(!initial);
  await flip(initial);
});

test("/unsubscribe: без валидного токена — «ссылка недействительна», без клика запроса нет", async ({
  page,
}) => {
  const posts: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/unsubscribe")) posts.push(r.url());
  });
  const result = page.getByTestId("unsubscribe-result");

  // Без токена в адресе: сразу «недействительна», кнопки нет.
  await page.goto("/unsubscribe");
  await expect(result).toHaveAttribute("data-state", "invalid");
  await expect(result).toContainText("Ссылка недействительна или устарела");
  await expect(page.getByTestId("unsubscribe-confirm")).toHaveCount(0);

  // Правдоподобный, но поддельный токен: запроса до клика нет, после клика — единое сообщение.
  for (const token of [`forged-${RUN}-0123456789`, `another-${RUN}-abcdefghij`]) {
    await page.goto(`/unsubscribe?token=${token}`);
    await expect(page.getByTestId("unsubscribe-confirm")).toBeVisible();
    expect(page.url()).not.toContain("token");
    expect(posts).toEqual([]);

    const [res] = await Promise.all([
      page.waitForResponse(
        (r) =>
          r.request().method() === "POST" &&
          new URL(r.url()).pathname === "/api/notifications/v1/unsubscribe",
      ),
      page.getByTestId("unsubscribe-confirm").click(),
    ]);
    expect(res.status(), await res.text()).toBeLessThan(500);
    expect(res.status()).not.toBe(200);
    expect(res.url()).not.toContain(token);
    await expect(result).toHaveAttribute("data-state", "invalid");
    await expect(result).toContainText("Ссылка недействительна или устарела");
    posts.length = 0;
  }
});
