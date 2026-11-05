/** Заголовки вкладки: несуществующий адрес и страница сборника называются по содержимому. */
import { expect, login, test } from "./fixtures";

test("несуществующий адрес: «Страница не найдена · Guessverse»", async ({ page }) => {
  const response = await page.goto("/no-such-page-xyz");
  expect(response?.status()).toBe(404);
  await expect(page).toHaveTitle("Страница не найдена · Guessverse");
});

test("страница сборника: заголовок вкладки — название сборника", async ({ page }) => {
  await login(page);
  await page.goto("/collections");
  const card = page.getByTestId(/^collection-card-/).first();
  await card.click();
  await expect(page.getByTestId("collection-modal")).toBeVisible();
  const name = (
    await page.getByTestId("collection-modal").getByRole("heading", { level: 1 }).innerText()
  ).trim();
  await expect(page).toHaveTitle(`${name} · Guessverse`);
});
