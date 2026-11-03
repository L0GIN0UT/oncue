import { expect, giveUpButton, isMove, test } from "./fixtures";

test("ревил через 2 минуты: свежая ссылка через final-clue", async ({ page, scenario }) => {
  // Как на реальном game: рядом с кадром ревила приходит постер (ADR 0073) — кадр важнее (ADR 0026).
  await scenario({ revealArtwork: true });
  // Часы браузера под контролем теста: ссылка ревила (TTL 60 с) «истекает» без ожидания.
  await page.clock.install();
  await page.goto("/play/film.random.blackout");
  await expect(page).toHaveURL(/\/play\/s\//);

  await Promise.all([
    page.waitForResponse((r) => isMove(r.url(), r.request().method())),
    giveUpButton(page).click(),
  ]);
  await expect(page.getByTestId("reveal-image")).toBeVisible();
  await expect(page.getByRole("img", { name: /^Постер/ })).toHaveCount(0);

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

test("музыка: аудио-ревил и обложка — обложка на месте, кадра нет", async ({ page, scenario }) => {
  await scenario({ revealArtwork: true });
  await page.goto("/play/music.random.classic");
  await expect(page).toHaveURL(/\/play\/s\//);
  await Promise.all([
    page.waitForResponse((r) => isMove(r.url(), r.request().method())),
    giveUpButton(page).click(),
  ]);
  await expect(page.getByRole("img", { name: /^Обложка/ })).toBeVisible();
  await expect(page.getByTestId("reveal-image")).toHaveCount(0);
});
