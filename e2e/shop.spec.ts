/**
 * Полный стек: магазин, оформление и промокоды 5b (ADR 0045-0048).
 *
 * Один последовательный сценарий на засеянном стеке (`gv dev seed`: `seeds/progression/items`,
 * конфиг с секцией `shop`). Игрок регистрируется через UI; Версы ему начисляет админ-контекст
 * (progression admin-API, бакет `earned`) — как у `gv dev seed`. Предметы берутся по ключам
 * сида (`seeds/progression/items/items.yaml`), а слоты, цены и картинки — из данных API
 * магазина (`/api/web/v1/pages/shop`), баланс сверяется по `/api/progression/v1/me/wallet`.
 *
 * Бюджет: выдача 1000 Версов; рамка frm_neon (200) + титул ttl_melomaniac (150) + титул
 * ttl_cinephile (150, покупка «с другой вкладки») = 500, остаток 500 < ava_dragon (600):
 * нехватка Версов. Затем промокод на 25 Версов.
 *
 * Ответы и тексты (кроме `data-testid`) взяты из `frontend/messages/ru.json` и сидов.
 */
import { randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import {
  ADMIN,
  HAS_ADMIN,
  NO_ADMIN_REASON,
  adminContext,
  grantVerses,
  userIdOf,
} from "./admin";
import { api, apiErrorsOf, closePlayers, json, problem, registerPlayer, RUN, type Player } from "./online";

test.skip(process.env.E2E_SEEDED !== "1", "стек не засеян: нужен шаг засева (E2E_SEEDED=1)");
test.skip(!HAS_ADMIN, NO_ADMIN_REASON);
test.describe.configure({ mode: "serial" });

// Ключи сидовых предметов (seeds/progression/items/items.yaml).
const FRAME = "frm_neon"; // рамка, animated, 200
const TITLE = "ttl_melomaniac"; // титул «Меломан», 150
const TITLE_RACE = "ttl_cinephile"; // титул «Киноман», 150
const EXPENSIVE = "ava_dragon"; // аватарка, 600
const GRANT = 1000;
const PROMO_VERSES = 25;

type Item = {
  id: string;
  key: string;
  slot: string;
  title: string;
  price: number | null;
  media_kind: "static" | "animated";
  asset_url: string;
};
type Catalog = {
  balance: number;
  slots: string[];
  items: { item: Item; state: "locked" | "available" | "owned" }[];
};
type Wallet = { total: number; earned: number; paid: number };

let player: Player;
let admin: Awaited<ReturnType<typeof adminContext>>;
let userId = "";

test.beforeAll(async ({ browser }) => {
  test.setTimeout(180_000);
  player = await registerPlayer(browser, "shop");
  admin = await adminContext(ADMIN.progression);
  userId = await userIdOf(player.page);
  await grantVerses(admin, userId, GRANT, `e2e: магазин ${RUN}`);
});

test.afterAll(async () => {
  await admin?.dispose();
  await closePlayers(player ? [player] : []);
});

test.afterEach(async ({}, testInfo) => {
  // Диагностика падения: последние ответы API с ошибкой по контексту игрока.
  if (player && testInfo.status !== testInfo.expectedStatus)
    await testInfo.attach("api-errors", { body: apiErrorsOf([player.page]) });
});

// ----------------------------------------------------------------------------- данные
async function catalogOf(page: Page): Promise<Catalog> {
  const shop = await json<{ blocks: { catalog: { status: string; data: Catalog | null } } }>(
    await page.request.get("/api/web/v1/pages/shop"),
  );
  expect(shop.blocks.catalog.status, "блок catalog витрины").toBe("ok");
  return shop.blocks.catalog.data!;
}

function itemOf(catalog: Catalog, key: string) {
  const entry = catalog.items.find((e) => e.item.key === key);
  expect(entry, `предмет сида ${key} в витрине`).toBeTruthy();
  return entry!;
}

const walletTotal = async (page: Page): Promise<number> =>
  (await json<Wallet>(await api(page, "progression").get("/me/wallet"))).total;

/** Подпись «Баланс: N Версов»; допускает разделители разрядов (пробел, U+00A0, U+202F). */
const SEP = "[\\s\\u00a0\\u202f]";
const balanceText = (n: number): RegExp =>
  new RegExp(`Баланс:${SEP}*${String(n).split("").join(`${SEP}?`)}${SEP}`);

async function openSlot(page: Page, slot: string): Promise<void> {
  await page.getByTestId(`shop-tab-${slot}`).click();
  await expect(page.getByTestId(`shop-tab-${slot}`)).toHaveAttribute("aria-selected", "true");
}

/** Покупка через UI: карточка -> «Купить» -> диалог -> «Подтвердить». */
async function buyViaUi(page: Page, slot: string, key: string): Promise<void> {
  await openSlot(page, slot);
  await page.getByTestId(`shop-buy-${key}`).click();
  await expect(page.getByTestId("shop-confirm-dialog")).toBeVisible();
  await page.getByTestId("shop-confirm").click();
}

/** Выбрать предмет (или «Пусто» при `label = null`) в слоте «Оформления» и дождаться сохранения. */
async function equip(page: Page, slot: string, label: string | null): Promise<void> {
  const [res] = await Promise.all([
    page.waitForResponse(
      (r) =>
        r.request().method() === "PUT" &&
        new URL(r.url()).pathname === `/api/progression/v1/me/appearance/${slot}`,
    ),
    page.getByTestId(`equip-${slot}`).selectOption(label === null ? { value: "" } : { label }),
  ]);
  expect(res.status(), await res.text()).toBe(200);
}

// ----------------------------------------------------------------------------- сценарий
test("витрина: вкладки слотов из данных, сидовые предметы, анимированный предмет рисуется", async () => {
  const { page } = player;
  await page.goto("/");
  await page.getByTestId("nav-shop").click();
  await expect(page).toHaveURL(/\/shop$/);

  const catalog = await catalogOf(page);
  expect(catalog.slots.length).toBeGreaterThan(0);
  await expect(page.getByTestId("shop-balance")).toHaveText(balanceText(GRANT));
  for (const slot of catalog.slots) await expect(page.getByTestId(`shop-tab-${slot}`)).toBeVisible();
  await expect(page.getByTestId("shop-tab-inventory")).toBeVisible();
  await expect(page.getByTestId("shop-tab-appearance")).toBeVisible();

  // Каждый сидовый предмет виден на вкладке своего слота.
  for (const key of [FRAME, TITLE, TITLE_RACE, EXPENSIVE]) {
    const { item } = itemOf(catalog, key);
    await openSlot(page, item.slot);
    await expect(page.getByTestId(`shop-item-${key}`)).toBeVisible();
  }

  // Анимированный предмет: <img> с asset_url из /cosmetics/, файл отдаётся.
  const neon = itemOf(catalog, FRAME).item;
  expect(neon.media_kind).toBe("animated");
  expect(neon.asset_url).toMatch(/^\/cosmetics\//);
  await openSlot(page, neon.slot);
  const img = page.getByTestId(`shop-item-${FRAME}`).locator("img");
  await expect(img).toHaveAttribute("src", neon.asset_url);
  const file = await page.request.get(neon.asset_url);
  expect(file.status(), neon.asset_url).toBe(200);
  expect(file.headers()["content-type"]).toContain("image/");
});

test("покупка: подтверждение, баланс уменьшился на цену, предмет куплен", async () => {
  const { page } = player;
  const catalog = await catalogOf(page);
  let expected = GRANT;
  for (const key of [FRAME, TITLE]) {
    const { item } = itemOf(catalog, key);
    expect(item.price, `цена ${key}`).toBeGreaterThan(0);
    await buyViaUi(page, item.slot, key);
    expected -= item.price!;
    await expect(page.getByTestId(`shop-item-${key}`)).toHaveAttribute("data-state", "owned");
    await expect(page.getByTestId("shop-status")).toContainText(`Куплено: ${item.title}`);
    await expect(page.getByTestId("shop-balance")).toHaveText(balanceText(expected));
    await expect(page.getByTestId(`shop-buy-${key}`)).toHaveCount(0);
    expect(await walletTotal(page)).toBe(expected);
  }
  // Купленное видно в инвентаре.
  await page.getByTestId("shop-tab-inventory").click();
  await expect(page.getByTestId(`inventory-item-${FRAME}`)).toBeVisible();
  await expect(page.getByTestId(`inventory-item-${TITLE}`)).toBeVisible();
});

test("повторная покупка того же предмета идемпотентна: «Куплено», Версы не списываются повторно", async () => {
  const { page } = player;
  const catalog = await catalogOf(page);
  const race = itemOf(catalog, TITLE_RACE).item;
  const before = await walletTotal(page);
  const after = before - race.price!;

  // Страница открыта с кнопкой «Купить», а предмет уже куплен «с другой вкладки» (через API).
  await page.goto("/shop");
  await openSlot(page, race.slot);
  await expect(page.getByTestId(`shop-buy-${TITLE_RACE}`)).toBeVisible();
  const bought = await json<{ balance_after: number; repeated: boolean }>(
    await api(page, "progression").post(`/shop/items/${race.id}/purchase`),
  );
  expect(bought.balance_after).toBe(after);
  expect(bought.repeated).toBe(false);

  // Сервис проверяет op_id покупки раньше доступности: повтор — 200 repeated=true, без ошибки.
  await page.getByTestId(`shop-buy-${TITLE_RACE}`).click();
  await page.getByTestId("shop-confirm").click();
  await expect(page.getByTestId("shop-status")).toContainText(`Куплено: ${race.title}`);
  await expect(page.getByTestId(`shop-item-${TITLE_RACE}`)).toHaveAttribute("data-state", "owned");
  await expect(page.getByTestId("shop-error")).toHaveCount(0);
  await expect(page.getByTestId("shop-balance")).toHaveText(balanceText(after));

  // То же на уровне API (новый ключ идемпотентности): 200, repeated, баланс после первой покупки.
  const again = await json<{ balance_after: number; repeated: boolean }>(
    await api(page, "progression").post(`/shop/items/${race.id}/purchase`),
  );
  expect(again.repeated).toBe(true);
  expect(again.balance_after).toBe(after);
  expect(await walletTotal(page)).toBe(after);
});

test("недостаточно Версов: insufficient_funds, баланс не меняется", async () => {
  const { page } = player;
  const catalog = await catalogOf(page);
  const dragon = itemOf(catalog, EXPENSIVE).item;
  const before = await walletTotal(page);
  expect(before, "остаток меньше цены дорогого предмета").toBeLessThan(dragon.price!);

  await page.goto("/shop");
  await buyViaUi(page, dragon.slot, EXPENSIVE);
  await expect(page.getByTestId("shop-error")).toContainText("Не хватает Версов");
  await page.getByRole("button", { name: "Отмена" }).click();
  await expect(page.getByTestId(`shop-item-${EXPENSIVE}`)).toHaveAttribute("data-state", "available");
  expect(await walletTotal(page)).toBe(before);
  await expect(page.getByTestId("shop-balance")).toHaveText(balanceText(before));

  // Тот же отказ на уровне API.
  const res = await problem(await api(page, "progression").post(`/shop/items/${dragon.id}/purchase`), 409);
  expect(res.code).toBe("progression.insufficient_funds");
  expect(await walletTotal(page)).toBe(before);
});

test("оформление: рамка и титул в профиле, снятие даёт пустой слот", async () => {
  const { page } = player;
  const catalog = await catalogOf(page);
  const frame = itemOf(catalog, FRAME).item;
  const title = itemOf(catalog, TITLE).item;

  await page.goto("/shop");
  await page.getByTestId("shop-tab-appearance").click();
  await expect(page.getByTestId(`appearance-slot-${frame.slot}`)).toBeVisible();
  await equip(page, frame.slot, frame.title);
  await equip(page, title.slot, title.title);

  await page.goto("/profile");
  const badge = page.getByTestId("player-badge").first();
  await expect(badge.locator(`img[src="${frame.asset_url}"]`)).toBeVisible();
  await expect(badge.getByTestId("player-title")).toHaveText(title.title);
  await expect(page.getByTestId("profile-backdrop")).toBeVisible();

  // Снять: слоты пустые, в профиле ни рамки, ни титула.
  await page.goto("/shop");
  await page.getByTestId("shop-tab-appearance").click();
  await expect(page.getByTestId(`equip-${frame.slot}`)).not.toHaveValue("");
  await equip(page, frame.slot, null);
  await equip(page, title.slot, null);
  await expect(page.getByTestId(`equip-${frame.slot}`)).toHaveValue("");
  await expect(page.getByTestId(`equip-${title.slot}`)).toHaveValue("");

  await page.goto("/profile");
  const empty = page.getByTestId("player-badge").first();
  await expect(empty.getByTestId("player-nickname")).toBeVisible();
  await expect(empty.locator(`img[src="${frame.asset_url}"]`)).toHaveCount(0);
  await expect(empty.getByTestId("player-title")).toHaveCount(0);
});

test("промокод: награда Версами, повтор не начисляет второй раз, неверный код — ошибка", async () => {
  const { page } = player;
  // Партия: один код, одно погашение, 25 Версов. Коды показываются один раз — в ответе создания.
  const created = await json<{ id: string; codes: string[] }>(
    await admin.post("/admin/v1/promo-codes", {
      headers: { "Idempotency-Key": randomUUID() },
      data: {
        label: `e2e ${RUN}`,
        reward: { kind: "verses", verses_amount: PROMO_VERSES },
        count: 1,
        max_uses: 1,
        reason: `e2e: промокод ${RUN}`,
      },
    }),
    201,
  );
  expect(created.codes).toHaveLength(1);
  const code = created.codes[0]!;
  const before = await walletTotal(page);

  const redeem = async (value: string) => {
    await page.getByTestId("promo-input").fill(value);
    const [res] = await Promise.all([
      page.waitForResponse(
        (r) =>
          r.request().method() === "POST" &&
          new URL(r.url()).pathname === "/api/progression/v1/promo/redeem",
      ),
      page.getByTestId("promo-submit").click(),
    ]);
    return res;
  };

  await page.goto("/profile");
  await expect(page.getByTestId("block-promo")).toBeVisible();
  const first = await redeem(code);
  expect(first.status(), await first.text()).toBe(200);
  await expect(page.getByTestId("promo-result")).toContainText(String(PROMO_VERSES));
  await expect(page.getByTestId("promo-error")).toHaveCount(0);
  await expect.poll(() => walletTotal(page), { message: "Версы за промокод" }).toBe(before + PROMO_VERSES);
  await expect(page.getByTestId("wallet-total")).toContainText(String(before + PROMO_VERSES));

  // Тот же игрок вводит тот же код ещё раз: та же награда с пометкой «повтор», второго начисления нет.
  const second = await redeem(code);
  expect(second.status(), await second.text()).toBe(200);
  expect(((await second.json()) as { repeated: boolean }).repeated).toBe(true);
  await expect(page.getByTestId("promo-result")).toContainText("Этот код вы уже использовали");
  await expect(page.getByTestId("promo-error")).toHaveCount(0);
  expect(await walletTotal(page)).toBe(before + PROMO_VERSES);

  // Неверный код.
  const bad = await redeem(`NO-SUCH-${RUN.toUpperCase()}`);
  expect(bad.status(), await bad.text()).toBe(404);
  await expect(page.getByTestId("promo-error")).toContainText("Код не подходит");
  expect(await walletTotal(page)).toBe(before + PROMO_VERSES);
});
