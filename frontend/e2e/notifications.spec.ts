/**
 * Уведомления на моках (этап 5b): колокольчик со счётчиком и realtime, список, «прочитать все»,
 * настройки, отписка по ссылке из письма (без входа, токен не остаётся в адресе) и тема из shell.
 * Против живых сервисов — `../e2e/notifications.spec.ts` (шаг 0b).
 */
import { expect, login, test } from "./fixtures";

const MOCK_URL = `http://127.0.0.1:${process.env.E2E_MOCK_PORT ?? 3101}`;

test("колокольчик: число из shell, realtime увеличивает, список и «прочитать все»", async ({
  page,
  request,
  expectAccessible,
}) => {
  await login(page);
  await expect(page.getByTestId("notification-count")).toHaveText("2");

  await request.post(`${MOCK_URL}/api/notifications/__e2e/push`, {
    data: { title: "Задание выполнено", body: "Сыграй три раунда" },
  });
  await expect(page.getByTestId("notification-count")).toHaveText("3");

  await page.getByTestId("notification-bell").click();
  await expect(page.getByTestId("notifications-panel")).toBeVisible();
  const items = page.getByTestId("notification-item");
  await expect(items).toHaveCount(4);
  await expect(items.first()).toContainText("Задание выполнено");
  await expectAccessible(page);

  await page.getByTestId("notifications-read-all").click();
  await expect(page.getByTestId("notification-count")).toHaveCount(0);
  await expect(page.locator("[data-testid=notification-item][data-read=false]")).toHaveCount(0);
  await expect(page.getByTestId("notifications-read-all")).toBeDisabled();
});

test("уведомление, пришедшее по каналу, появляется в открытом списке", async ({
  page,
  request,
}) => {
  await login(page);
  await page.goto("/notifications");
  await expect(page.getByTestId("notification-item")).toHaveCount(3);
  await request.post(`${MOCK_URL}/api/notifications/__e2e/push`, { data: { title: "Новинка" } });
  await expect(page.getByTestId("notification-item").first()).toContainText("Новинка");
  await expect(page.getByTestId("notification-count")).toHaveText("3");
});

test("прочитать одно уведомление уменьшает счётчик", async ({ page }) => {
  await login(page);
  await page.goto("/notifications");
  await page.getByTestId("notification-item").first().getByRole("button").click();
  await expect(page.getByTestId("notification-count")).toHaveText("1");
});

test("настройки: тип и согласие на рассылку", async ({ page, expectAccessible }) => {
  await login(page);
  await page.goto("/notifications/settings");
  const box = page.getByRole("checkbox", { name: "Новый уровень — По почте" });
  await box.check();
  await expect(box).toBeChecked();
  await page.getByTestId("email-consent").check();
  await expect(page.getByTestId("email-consent-state")).toContainText("Вы подписаны");
  await page.reload();
  await expect(page.getByRole("checkbox", { name: "Новый уровень — По почте" })).toBeChecked();
  await expect(page.getByTestId("email-consent")).toBeChecked();
  await expectAccessible(page);
});

test("отписка по ссылке из письма: без входа, токен убран из адреса, запрос только по клику", async ({
  page,
  expectAccessible,
}) => {
  const posts: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/unsubscribe")) posts.push(r.url());
  });
  await page.goto("/unsubscribe?token=good-token-0123456789");
  await expect(page.getByTestId("unsubscribe-confirm")).toBeVisible();
  expect(page.url()).not.toContain("token");
  expect(page.url()).toMatch(/\/unsubscribe$/);
  // Без клика (как у почтового сканера) отписки нет.
  await page.waitForTimeout(500);
  expect(posts).toEqual([]);
  await expectAccessible(page);

  await page.getByTestId("unsubscribe-confirm").click();
  await expect(page.getByTestId("unsubscribe-result")).toContainText("Вы отписаны");
  expect(posts).toHaveLength(1);
  expect(posts[0]).not.toContain("token");
});

test("отписка: любой неверный токен — одно сообщение; 429 — повторить позже", async ({ page }) => {
  const result = page.getByTestId("unsubscribe-result");
  for (const token of ["forged-token-0123456789", "another-bad-token-012345"]) {
    await page.goto(`/unsubscribe?token=${token}`);
    await page.getByTestId("unsubscribe-confirm").click();
    await expect(result).toContainText("Ссылка недействительна или устарела");
  }
  await page.goto("/unsubscribe?token=limited-token-0123456789");
  await page.getByTestId("unsubscribe-confirm").click();
  await expect(result).toHaveAttribute("data-state", "later");
});

test("отписка: ответ страницы запрещает Referer, токен не уходит наружу", async ({ page }) => {
  const response = await page.goto("/unsubscribe?token=good-token-0123456789");
  expect(response?.headers()["referrer-policy"]).toBe("no-referrer");
  await expect(page.locator('meta[name="referrer"]')).toHaveAttribute("content", "no-referrer");
  const referers: string[] = [];
  page.on("request", (r) => {
    const ref = r.headers()["referer"];
    if (ref) referers.push(ref);
  });
  await page.reload();
  expect(referers.filter((r) => r.includes("token"))).toEqual([]);
});

test("тема: data-theme=default; неизвестный ключ и сбой shell — default, страница цела", async ({
  page,
  scenario,
}) => {
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "default");

  await scenario({ themeKey: "no-such-theme" });
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "default");

  await scenario({ themeKey: "default", shellDown: true });
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "default");
  await expect(page.getByRole("banner").getByRole("link", { name: "Войти" })).toBeVisible();
});
