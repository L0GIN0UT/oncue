/**
 * Итоги: обложки строк и перевыдача истёкшей ссылки; предзагрузка уровня 1 следующего раунда
 * (`next_round_first_clue`). Проверяется итог (картинка загружена, файл скачан один раз), а не тайминг.
 */
import { expect, giveUpRemaining, isMove, login, test } from "./fixtures";

test("итоги: у каждой строки обложка из artwork (картинка загружена)", async ({
  page,
  scenario,
}) => {
  await scenario({ revealArtwork: true });
  await page.goto("/play/music.daily.classic");
  await giveUpRemaining(page);
  const rows = page.getByTestId("result-row");
  await expect(rows).toHaveCount(5);
  for (let i = 0; i < 5; i++) {
    const img = rows.nth(i).locator("img").first();
    await expect(img).toBeVisible();
    await expect
      .poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth))
      .toBeGreaterThan(0);
  }
});

test("итоги: ссылка обложки не открылась (403) → перевыдача, картинка загружена", async ({
  page,
  scenario,
}) => {
  await scenario({ revealArtwork: true, staleArtworkLinks: true });
  const issued: number[] = [];
  page.on("response", (r) => {
    if (/\/rounds\/\d+\/artwork$/.test(r.url()) && r.request().method() === "POST")
      issued.push(r.status());
  });
  await page.goto("/play/music.daily.classic");
  await giveUpRemaining(page);
  const rows = page.getByTestId("result-row");
  await expect(rows).toHaveCount(5);
  for (let i = 0; i < 5; i++) {
    const img = rows.nth(i).locator("img").first();
    await expect
      .poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth))
      .toBeGreaterThan(0);
  }
  // По одной перевыдаче на строку, без повторов.
  expect(issued).toEqual([200, 200, 200, 200, 200]);
});

test("предзагрузка: файл уровня 1 следующего раунда качается на ответе, раунд стартует обычным /clue", async ({
  page,
  scenario,
}) => {
  await scenario({ nextRoundPreload: true });
  await login(page);
  let preloaded: string | null = null;
  page.on("response", async (r) => {
    if (!isMove(r.url(), r.request().method())) return;
    const body = (await r.json().catch(() => null)) as {
      next_round_first_clue?: { url: string } | null;
    } | null;
    if (body?.next_round_first_clue && !preloaded)
      preloaded = new URL(body.next_round_first_clue.url, page.url()).pathname;
  });
  const media: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "GET" && r.url().includes("/media/a/"))
      media.push(new URL(r.url()).pathname);
  });
  const clueCalls: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && /\/rounds\/1\/clue$/.test(r.url())) clueCalls.push(r.url());
  });

  await page.goto("/play/music.random.classic");
  await expect(page.getByTestId("clue-audio")).toBeAttached();
  // Пропуски до последнего уровня и «Сдаться».
  const skip = page.getByTestId("skip");
  while (
    !(await page
      .getByTestId("give-up")
      .isEnabled()
      .catch(() => false))
  ) {
    await Promise.all([
      page.waitForResponse((r) => isMove(r.url(), r.request().method())),
      skip.click(),
    ]);
  }
  await Promise.all([
    page.waitForResponse((r) => isMove(r.url(), r.request().method())),
    page.getByTestId("give-up").click(),
  ]);
  await expect(page.getByTestId("reveal")).toBeVisible();

  // Файл следующего раунда скачан до того, как раунд открыт запросом.
  await expect.poll(() => preloaded !== null && media.includes(preloaded)).toBe(true);
  expect(clueCalls).toHaveLength(0);

  await page.getByTestId("reveal-next").click();
  await expect(page.getByTestId("clue-audio")).toHaveAttribute("src", /^blob:/);
  expect(clueCalls).toHaveLength(1);
  expect(media.filter((u) => u === preloaded)).toHaveLength(1);
});
