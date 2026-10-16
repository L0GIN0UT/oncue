import { expect, giveUpButton, isMove, test } from "./fixtures";

test("ревил через 2 минуты: свежая ссылка через final-clue", async ({ page }) => {
  // Часы браузера под контролем теста: ссылка ревила (TTL 60 с) «истекает» без ожидания.
  await page.clock.install();
  await page.goto("/play/film.random.blackout");
  await expect(page).toHaveURL(/\/play\/s\//);

  await Promise.all([
    page.waitForResponse((r) => isMove(r.url(), r.request().method())),
    giveUpButton(page).click(),
  ]);
  await expect(page.getByTestId("reveal-image")).toBeVisible();

  const refreshed = page.waitForRequest(
    (r) => r.method() === "POST" && /\/rounds\/0\/final-clue$/.test(r.url()),
  );
  await page.clock.fastForward("02:00");
  await refreshed;
  await expect(page.getByTestId("reveal-image")).toHaveAttribute("src", /^blob:/);
});

test("регресс хвоста этапа 2: старая ссылка ревила 403 → кадр загружен со второй", async ({
  page,
  scenario,
}) => {
  await scenario({ staleRevealLinks: true });
  const finalClue: number[] = [];
  page.on("response", (r) => {
    if (/\/rounds\/\d+\/final-clue$/.test(r.url())) finalClue.push(r.status());
  });
  await page.goto("/play/film.daily.frame");
  await Promise.all([
    page.waitForResponse((r) => isMove(r.url(), r.request().method())),
    giveUpButton(page).click(),
  ]);
  await expect(page.getByTestId("reveal-image")).toHaveAttribute("src", /^blob:/);
  expect(finalClue).toEqual([200]);
});
