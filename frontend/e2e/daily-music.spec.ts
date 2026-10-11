import { answerWithSuggestion, expect, giveUpRemaining, test } from "./fixtures";

test("гость проходит дейлик музыки, делится итогом и не может переиграть", async ({
  page,
  context,
  expectAccessible,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/");
  await expect(page.getByTestId("daily-music.daily.classic")).toBeVisible();
  await expect(page.getByTestId("daily-film.daily.frame")).toBeVisible();
  await expect(page.getByTestId("countdown")).toHaveText(/^\d{2}:\d{2}:\d{2}$/);
  await expectAccessible(page);

  await page.getByTestId("daily-music.daily.classic").getByRole("link", { name: "Играть" }).click();
  await expect(page).toHaveURL(/\/play\/s\//);
  await expect(page.getByRole("heading", { name: "Раунд 1 из 5" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Слушать 0,5 с" })).toBeVisible();
  await expectAccessible(page);

  // Неверный ответ → уровень 2.
  await page.getByRole("combobox").fill("совсем не то");
  await page.getByRole("button", { name: "Ответить" }).click();
  await expect(page.getByTestId("skip")).toHaveText(/Пропустить \(\+1 с\)/);
  // Пропуск → уровень 3.
  await page.getByTestId("skip").click();
  await expect(page.getByRole("button", { name: "Слушать 2 с" })).toBeVisible();
  // Верный ответ из автокомплита → ревил.
  await answerWithSuggestion(page, "Групп", "Группа крови");
  const reveal = page.getByTestId("reveal");
  await expect(reveal).toContainText("Угадано с 3-й попытки");
  await expect(reveal).toContainText("+4");
  await expect(reveal.getByRole("heading", { name: "Группа крови" })).toBeVisible();
  await expect(reveal).toContainText("Кино");
  await expectAccessible(page);

  await giveUpRemaining(page);
  await expect(page).toHaveURL(/\/results\//);
  await expect(page.getByTestId("final-score")).toContainText("4");
  await expect(page.getByTestId("final-score")).toContainText("/ 30");
  await expect(page.getByTestId("result-row")).toHaveCount(5);
  await expect(page.getByText("угадали 63% игроков")).toBeVisible();
  await expect(page.getByRole("link", { name: "Войти" }).last()).toBeVisible();
  await expectAccessible(page);

  await page.getByTestId("share").click();
  await expect(page.getByText("Результат скопирован")).toBeVisible();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toContain("🟩");
  expect(copied).toContain("http://localhost:");

  // Повторный вход в пройденный дейлик открывает итог.
  await page.goto("/");
  const card = page.getByTestId("daily-music.daily.classic");
  await expect(card).toContainText("Ваш результат: 4 из 30");
  await page.goto("/play/music.daily.classic");
  await expect(page).toHaveURL(/\/results\//);
  await expect(page.getByTestId("final-score")).toContainText("4");
});

test("ходы игры отправляются с CSRF и Idempotency-Key, ассет — blob", async ({ page }) => {
  const moves: { csrf: string | null; key: string | null }[] = [];
  page.on("request", (r) => {
    if (/\/api\/game\/v1\/sessions\/[^/]+\/(skips|guesses)/.test(r.url()))
      moves.push({
        csrf: r.headers()["x-csrf-token"] ?? null,
        key: r.headers()["idempotency-key"] ?? null,
      });
  });
  await page.goto("/play/music.daily.classic");
  await expect(page.getByTestId("clue-audio")).toHaveAttribute("src", /^blob:/);
  await page.getByTestId("skip").click();
  await expect(page.getByTestId("skip")).toHaveText(/\+1 с/);
  expect(moves).toHaveLength(1);
  expect(moves[0]!.csrf).toBeTruthy();
  expect(moves[0]!.key).toMatch(/^[0-9a-f-]{36}$/);
});
