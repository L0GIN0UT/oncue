/**
 * Полный стек: гость проходит «Трек дня», античит-инварианты видны снаружи (план §0 п. 2–3).
 * Ответы неизвестны тесту, поэтому раунды завершаются через «Сдаться».
 */
import { expect, test, type Page } from "@playwright/test";

const DAILY = "music.daily.classic";

// Сценарию нужен засеянный стек: каталог, исходники медиа и готовые ассеты, наборы дня.
// Пока засева нет (docs/09-roadmap.md, «Хвосты этапа 2»), тест явно помечается пропущенным
// с причиной — CI включает его переменной E2E_SEEDED=1 после шага засева.
test.skip(process.env.E2E_SEEDED !== "1", "стек не засеян: нужен шаг засева (E2E_SEEDED=1)");

async function finishByGivingUp(page: Page): Promise<void> {
  for (;;) {
    const toResults = page.getByRole("button", { name: "К итогам" });
    const next = page.getByRole("button", { name: "Продолжить" });
    const giveUp = page.locator("[data-testid=give-up]:enabled");
    await expect(toResults.or(next).or(giveUp).first()).toBeVisible();
    if (await toResults.isVisible()) return toResults.click();
    if (await next.isVisible()) {
      await next.click();
      continue;
    }
    await giveUp.click();
  }
}

test("гость: дейлик музыки от главной до итога, повторный вход ведёт на итог", async ({ page }) => {
  const snapshots: string[] = [];
  const clueUrls: string[] = [];
  // Тело читаем в try/catch: ответ, пришедший при уходе страницы (главная → /play), Chromium
  // уже освободил («No resource with given identifier found»). Необработанная ошибка в
  // обработчике роняла весь тест; потерянное тело не нужно — хватает дочитанных до навигации.
  page.on("response", async (r) => {
    const url = r.url();
    try {
      if (/\/api\/game\/v1\/sessions\/[^/]+$/.test(url) && r.request().method() === "GET")
        snapshots.push(await r.text());
      if (/\/rounds\/\d+\/clue$/.test(url) && r.ok())
        clueUrls.push(((await r.json()) as { url: string }).url);
    } catch {
      // тело недоступно: страница ушла дальше
    }
  });

  // Главная — хаб: плитка дейлика ведёт на /play/<режим>, сессию `/` не стартует.
  await page.goto("/");
  await expect(page.getByTestId("home-daily-music")).toHaveAttribute("href", `/play/${DAILY}`);
  await expect(page).toHaveURL("/");

  await page.goto(`/play/${DAILY}`);
  await expect(page).toHaveURL(/\/play\/s\//);
  const sessionId = page.url().split("/").pop()!;
  await expect(page.getByRole("button", { name: "Слушать 0,5 с" })).toBeVisible();

  // Ссылка ассета — подписанная /media/a/<ключ>.<ext> конкретного уровня.
  await expect.poll(() => clueUrls.length).toBeGreaterThan(0);
  expect(clueUrls[0]).toMatch(/^\/media\/a\/[A-Za-z0-9_-]+\.[a-z0-9]{2,5}\?/);
  // До ревила в снимке нет ответа.
  expect(snapshots.join("")).not.toMatch(/media_item_id|"title"/);

  // Ассет уровня выше текущего — 403 game.level_locked.
  // Запрос собираем руками, поэтому Origin ставим сами: cookie-мутации требуют Origin из
  // GV_PUBLIC_ORIGINS вместе с X-CSRF-Token (иначе 403 auth.csrf_failed ещё до game). У
  // запросов из браузера Origin проставляется автоматически — здесь берём его из адреса.
  const csrf = (await page.context().cookies()).find((c) => c.name === "gv_csrf")?.value ?? "";
  const origin = new URL(page.url()).origin;
  const locked = await page.request.post(`/api/game/v1/sessions/${sessionId}/rounds/0/clue`, {
    data: { level: 3 },
    headers: { "X-CSRF-Token": csrf, Origin: origin },
  });
  expect(locked.status()).toBe(403);
  expect(((await locked.json()) as { code: string }).code).toBe("game.level_locked");

  await finishByGivingUp(page);
  await expect(page).toHaveURL(new RegExp(`/results/${sessionId}`));
  await expect(page.getByTestId("final-score")).toContainText("/ 30");
  await expect(page.getByTestId("result-row")).toHaveCount(5);

  await page.goto(`/play/${DAILY}`);
  await expect(page).toHaveURL(new RegExp(`/results/${sessionId}`));
});
