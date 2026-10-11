import { expect, giveUpRemaining, test } from "./fixtures";

test("случайный режим с пресетом «2000-е» стартует и проходится", async ({
  page,
  expectAccessible,
}) => {
  await page.goto("/solo");
  const mode = page.getByTestId("random-music.random.classic");
  await expect(mode).toContainText("10 раундов");
  await expectAccessible(page);
  await mode.getByRole("link", { name: "2000-е" }).click();
  await expect(page).toHaveURL(/\/play\/s\//);
  await expect(page.getByRole("heading", { name: "Раунд 1 из 10" })).toBeVisible();
  await giveUpRemaining(page);
  await expect(page).toHaveURL(/\/results\//);
  await expect(page.getByTestId("result-row")).toHaveCount(10);
  // У случайного режима нет статистики дня.
  await expect(page.getByText(/угадали \d+% игроков/)).toHaveCount(0);
});
