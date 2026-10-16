import { expect, isMove, pickButton, playUntilResults, test } from "./fixtures";

test("сетка недели: туры до чемпиона, «согласны N %», повторный вход — итог", async ({
  page,
  context,
  expectAccessible,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/");
  await page
    .getByTestId("daily-film.bracket.weekly")
    .getByRole("link", { name: /Играть|Продолжить/ })
    .click();
  await expect(page).toHaveURL(/\/play\/s\//);
  const sessionId = page.url().split("/").pop()!;

  // Мок-сетка из 8: первый тур — 1/4 финала, варианты — две единицы с кадрами.
  await expect(page.getByTestId("bracket-stage")).toContainText("1/4 финала");
  await expect(pickButton(page)).toHaveCount(2);
  await expectAccessible(page);

  await Promise.all([
    page.waitForResponse((r) => isMove(r.url(), r.request().method())),
    page.keyboard.press("ArrowRight"),
  ]);
  await expect(page.getByTestId("agree-pct")).toContainText(/Согласны \d+ % игроков/);
  await expectAccessible(page);

  await playUntilResults(page, pickButton(page));
  await expect(page).toHaveURL(new RegExp(`/results/${sessionId}`));
  await expect(page.getByTestId("bracket-champion")).toBeVisible();
  await expect(page.getByTestId("bracket-pick")).toHaveCount(7);
  await expectAccessible(page);

  await page.getByTestId("share").click();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toMatch(/Мой чемпион: «.+» 🏆/);

  // Одна попытка на период: повторный вход ведёт на итог.
  await page.goto("/play/film.bracket.weekly");
  await expect(page).toHaveURL(new RegExp(`/results/${sessionId}`));
});
