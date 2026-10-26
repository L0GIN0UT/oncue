import { expect, giveUpRemaining, test } from "./fixtures";
import { openSoloMode } from "./hubs";

test("случайный режим с пресетом «2000-е» стартует и проходится", async ({
  page,
  expectAccessible,
}) => {
  const mode = await openSoloMode(page, "music.random.classic");
  await expect(mode).toContainText("10 раундов");
  await expectAccessible(page);
  await mode.getByRole("button", { name: "2000-е" }).click();
  await mode.getByRole("link", { name: "Играть" }).click();
  await expect(page).toHaveURL(/\/play\/s\//);
  await expect(page.getByRole("heading", { name: "Раунд 1 из 10" })).toBeVisible();
  await giveUpRemaining(page);
  await expect(page).toHaveURL(/\/results\//);
  await expect(page.getByTestId("result-row")).toHaveCount(10);
  // У случайного режима нет статистики дня.
  await expect(page.getByText(/угадали \d+% игроков/)).toHaveCount(0);
});
