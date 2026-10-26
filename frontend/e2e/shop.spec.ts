/**
 * Магазин, оформление, промокод и своя аватарка на моках (этап 5b): вкладки по слотам, покупка
 * с подтверждением, надеть/снять, ник + аватар + рамка + титул в профиле, промокод, загрузка
 * аватарки. Против живых сервисов — `../e2e/shop.spec.ts` (шаг 0b).
 */
import { expect, login, test, openMenuItem } from "./fixtures";

const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAH6u5tAAAAABJRU5ErkJggg==",
  "base64",
);

test("гость: магазин предлагает завести аккаунт", async ({ page }) => {
  await page.goto("/shop");
  await expect(page.getByTestId("profile-gate")).toBeVisible();
  await expect(page.getByTestId("nav-shop")).toHaveCount(0);
});

test("магазин: вкладки слотов, состояния карточек", async ({ page, expectAccessible }) => {
  await login(page);
  await openMenuItem(page, "nav-profile");
  await page.getByTestId("nav-shop").click();
  await expect(page).toHaveURL(/\/shop$/);
  await expect(page.getByTestId("shop-balance")).toContainText("40");

  await expect(page.getByTestId("shop-item-fox")).toHaveAttribute("data-state", "available");
  await page.getByTestId("shop-tab-frame").click();
  await expect(page.getByTestId("shop-item-gold_frame")).toHaveAttribute("data-state", "available");
  await expect(page.getByTestId("shop-item-neon_frame")).toHaveAttribute("data-state", "locked");
  await expect(page.getByTestId("shop-buy-neon_frame")).toHaveCount(0);
  await expectAccessible(page);
});

test("покупка: подтверждение, баланс и состояние обновляются; нехватка Версов — сообщение", async ({
  page,
}) => {
  await login(page);
  await page.goto("/shop");
  await page.getByTestId("shop-buy-fox").click();
  await page.getByTestId("shop-confirm").click();
  await expect(page.getByTestId("shop-item-fox")).toHaveAttribute("data-state", "owned");
  await expect(page.getByTestId("shop-balance")).toContainText("20");
  await expect(page.getByTestId("shop-status")).toContainText("Куплено: Лис");

  await page.getByTestId("shop-buy-owl").click();
  await page.getByTestId("shop-confirm").click();
  await expect(page.getByTestId("shop-error")).toContainText("Не хватает Версов");
  await expect(page.getByTestId("shop-balance")).toContainText("20");
});

test("оформление: надеть предмет — виден в карточке профиля; снять — пропадает", async ({
  page,
  expectAccessible,
}) => {
  await login(page);
  await page.goto("/shop");
  await page.getByTestId("shop-buy-fox").click();
  await page.getByTestId("shop-confirm").click();
  await expect(page.getByTestId("shop-item-fox")).toHaveAttribute("data-state", "owned");

  await page.getByTestId("shop-tab-inventory").click();
  await expect(page.getByTestId("inventory-item-fox")).toBeVisible();
  await page.getByTestId("shop-tab-appearance").click();
  await page.getByTestId("equip-avatar").selectOption({ label: "Лис" });
  await expectAccessible(page);

  await page.goto("/profile");
  await expect(page.getByTestId("player-badge").locator('img[src*="fox"]')).toBeVisible();

  await page.goto("/shop");
  await page.getByTestId("shop-tab-appearance").click();
  await page.getByTestId("equip-avatar").selectOption({ label: "Пусто" });
  await page.goto("/profile");
  await expect(page.getByTestId("player-badge")).toBeVisible();
  await expect(page.getByTestId("player-badge").locator("img")).toHaveCount(0);
});

test("промокод: награда Версами, неверный код — одно сообщение, 429 — отсчёт", async ({
  page,
  scenario,
  expectAccessible,
}) => {
  await login(page);
  await page.goto("/profile");
  await page.getByTestId("promo-input").fill("welcome");
  await page.getByTestId("promo-submit").click();
  await expect(page.getByTestId("promo-result")).toContainText("25 Версов");
  await expect(page.getByTestId("wallet-total")).toContainText("65");

  await page.getByTestId("promo-input").fill("nonsense");
  await page.getByTestId("promo-submit").click();
  await expect(page.getByTestId("promo-error")).toContainText("Код не подходит");
  await expectAccessible(page);

  await scenario({ promoRateLimited: true });
  await page.getByTestId("promo-input").fill("welcome");
  await page.getByTestId("promo-submit").click();
  await expect(page.getByTestId("promo-countdown")).toBeVisible();
  await expect(page.getByTestId("promo-submit")).toBeDisabled();
  await expect(page.getByTestId("promo-submit")).toBeEnabled({ timeout: 6_000 });
});

test("своя аватарка: без права — в магазин; купили — загрузка уходит на проверку", async ({
  page,
  expectAccessible,
}) => {
  await login(page);
  await page.goto("/profile");
  await expect(page.getByTestId("avatar-status")).toHaveAttribute("data-status", "none");
  await expect(page.getByTestId("avatar-upload")).toHaveCount(0);

  await page.getByTestId("promo-input").fill("welcome");
  await page.getByTestId("promo-submit").click();
  await expect(page.getByTestId("promo-result")).toBeVisible();
  await page.goto("/shop");
  await page.getByTestId("shop-buy-custom_avatar").click();
  await page.getByTestId("shop-confirm").click();
  await expect(page.getByTestId("shop-item-custom_avatar")).toHaveAttribute("data-state", "owned");

  await page.goto("/profile");
  await page
    .getByTestId("avatar-upload")
    .setInputFiles({ name: "me.png", mimeType: "image/png", buffer: PNG });
  await expect(page.getByRole("img", { name: "Предпросмотр" })).toBeVisible();
  await page.getByRole("button", { name: "Отправить на проверку" }).click();
  await expect(page.getByTestId("avatar-sent")).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("avatar-status")).toHaveAttribute("data-status", "pending");
  await expectAccessible(page);
});

test("своя аватарка: слишком большой файл отклоняется до отправки; отклонённая заявка — причина", async ({
  page,
  scenario,
}) => {
  await scenario({ avatarRequest: "rejected" });
  await login(page);
  await page.goto("/profile");
  const status = page.getByTestId("avatar-status");
  await expect(status).toHaveAttribute("data-status", "rejected");
  await expect(status).toContainText("Неприемлемое содержание");

  await page.getByTestId("avatar-upload").setInputFiles({
    name: "big.png",
    mimeType: "image/png",
    buffer: Buffer.alloc(1_100_000, 1),
  });
  await expect(page.getByTestId("avatar-error")).toContainText("больше допустимого размера");
});

test("блоки 5b недоступны — профиль цел", async ({ page, scenario }) => {
  await scenario({ appearanceDown: true });
  await login(page);
  await page.goto("/profile");
  await expect(page.getByTestId("level-value")).toBeVisible();
  await expect(page.getByTestId("block-avatar-unavailable")).toBeVisible();
  await expect(page.getByTestId("player-badge")).toBeVisible();
});
