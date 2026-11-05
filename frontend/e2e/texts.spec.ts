/** Тексты экранов: ничего не сказано дважды рядом. */
import { expect, login, test } from "./fixtures";

test("онлайн: у комнаты для друзей «до 6» сказано один раз", async ({ page }) => {
  await login(page);
  await page.goto("/online");
  const room = page.getByTestId("online-room");
  await expect(room).toBeVisible();
  await expect(room.getByText(/до 6/)).toHaveCount(1);
});

test("сборники: у «Сыграть компанией» лимит «до 6» назван один раз", async ({ page }) => {
  await login(page);
  await page.goto("/collections");
  await expect(page.getByTestId("catalog-together").getByText(/до 6/)).toHaveCount(1);
});

test("друзья: вкладка переживает перезагрузку и открывается по ссылке", async ({ page }) => {
  await login(page);
  await page.goto("/friends");
  await page.getByTestId("friends-tab-requests").click();
  await expect(page).toHaveURL(/\/friends\?tab=requests$/);
  await page.reload();
  await expect(page.getByRole("tab", { name: /Заявки/ })).toHaveAttribute("aria-selected", "true");
  await page.goto("/friends?tab=privacy");
  await expect(page.getByRole("tab", { name: "Приватность" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

test("соло: вкладка «Кино» переживает перезагрузку", async ({ page }) => {
  await login(page);
  await page.goto("/solo");
  await page.getByRole("radio", { name: "Кино" }).click();
  await expect(page).toHaveURL(/\/solo\?tab=film$/);
  await page.reload();
  await expect(page.getByRole("radio", { name: "Кино" })).toBeChecked();
  await expect(page.getByTestId("mode-card-film.random.frame")).toBeVisible();
});
