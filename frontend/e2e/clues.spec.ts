import { expect, giveUpButton, playUntilResults, test } from "./fixtures";
import { openSoloMode } from "./hubs";

test("витрина /solo: фильтр видов, сетка недели плиткой", async ({ page, expectAccessible }) => {
  for (const key of [
    "film.marathon.director",
    "music.marathon.artist",
    "film.pair.rating",
    "music.pair.popularity",
    "film.random.description",
    "film.random.blackout",
    "film.random.soundtrack",
  ]) {
    await expect(await openSoloMode(page, key), key).toBeVisible();
  }
  await expect(page.getByRole("tab", { name: "Марафоны" })).toBeVisible();
  await page.getByRole("radio", { name: "Кино" }).click();
  await expect(page.getByTestId("daily-film.bracket.weekly")).toBeVisible();
  await expectAccessible(page);
});

test("описание: уровень — текстовый JSON-ассет, поля копятся от уровня к уровню", async ({
  page,
  expectAccessible,
}) => {
  const clueUrls: string[] = [];
  page.on("response", async (r) => {
    if (/\/rounds\/\d+\/clue$/.test(r.url()) && r.ok())
      clueUrls.push(((await r.json()) as { url: string }).url);
  });
  await page.goto("/play/film.random.description");
  await expect(page).toHaveURL(/\/play\/s\//);
  const textClue = page.getByTestId("text-clue");
  await expect(textClue).toContainText(/Синтетическая история №\d+/);
  expect(clueUrls[0]).toMatch(/^\/media\/a\/[0-9a-f]+\.json\?/);
  await expectAccessible(page);

  await page.getByTestId("skip").click();
  await expect(textClue).toContainText("Герой ███");
  await page.getByTestId("skip").click();
  await expect(textClue).toContainText("Год");

  await playUntilResults(page, giveUpButton(page));
  await expect(page.getByTestId("result-row")).toHaveCount(5);
});

test("эмодзи: крупные эмодзи с подписью, с каждым уровнем — больше", async ({ page }) => {
  await page.goto("/play/film.random.emoji");
  await expect(page.getByRole("img", { name: /^Эмодзи \(1\)/ })).toBeVisible();
  await page.getByTestId("skip").click();
  await expect(page.getByRole("img", { name: /^Эмодзи \(2\)/ })).toBeVisible();
});

test("звук в темноте: чёрная сцена без автозапуска, ревил — кадр", async ({
  page,
  expectAccessible,
}) => {
  await page.goto("/play/film.random.blackout");
  const scene = page.getByTestId("clue-scene");
  await expect(scene).toHaveAttribute("data-scene", "blackout");
  await expect(page.getByTestId("clue-audio")).toHaveAttribute("src", /^blob:/);
  // Автовоспроизведения нет: звук — только по жесту.
  expect(await page.getByTestId("clue-audio").evaluate((a: HTMLAudioElement) => a.paused)).toBe(
    true,
  );
  await expectAccessible(page);
  await giveUpButton(page).click();
  await expect(page.getByTestId("reveal-image")).toHaveAttribute("src", /^blob:/);
});

test("кадр за кадром: уровень открывает другой кадр", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/play/film.random.sequence");
  const frame = page.getByTestId("clue-image");
  await expect(page.getByTestId("clue-image-hint")).toHaveText("Кадр 1");
  const first = await frame.getAttribute("src");
  await page.getByTestId("skip").click();
  await expect(page.getByTestId("clue-image-hint")).toHaveText("Кадр 2");
  expect(await frame.getAttribute("src")).not.toBe(first);
});

for (const [mode, node] of [
  ["film.random.frame", "clue-image"],
  ["music.random.classic", "clue-audio"],
] as const) {
  test(`${mode}: «Пропустить» не схлопывает страницу и не роняет прокрутку`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 600 });
    await page.goto(`/play/${mode}`);
    const clue = page.getByTestId(node);
    await expect(clue).toBeAttached();
    await clue.evaluate((el) => {
      // Любое удаление узла загадки и самая низкая высота страницы за время смены уровня.
      const w = window as unknown as { __flick: { removed: number; minHeight: number } };
      w.__flick = { removed: 0, minHeight: document.documentElement.scrollHeight };
      new MutationObserver(() => {
        if (!el.isConnected) w.__flick.removed += 1;
      }).observe(document.body, { childList: true, subtree: true });
      const sample = () => {
        w.__flick.minHeight = Math.min(w.__flick.minHeight, document.documentElement.scrollHeight);
        requestAnimationFrame(sample);
      };
      sample();
    });
    const before = await page.evaluate(() => document.documentElement.scrollHeight);
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    const scrolled = await page.evaluate(() => window.scrollY);

    await page.getByTestId("skip").click();
    // Итог: пропуск записан в историю, а не «подождали N мс».
    await expect(page.getByTestId("attempts").locator('[data-result="skip"]')).toHaveCount(1);

    const flick = await page.evaluate(
      () => (window as unknown as { __flick: { removed: number; minHeight: number } }).__flick,
    );
    expect(flick.removed).toBe(0);
    expect(flick.minHeight).toBeGreaterThanOrEqual(before);
    expect(await page.evaluate(() => window.scrollY)).toBeGreaterThanOrEqual(scrolled);
    await expect(clue).toBeAttached();
  });
}

test("music.random.classic: перематывают по ленте подсказок", async ({ page }) => {
  await page.goto("/play/music.random.classic");
  await expect(page.getByTestId("clue-audio")).toBeAttached();
  const tape = page.getByRole("slider", { name: "Перемотка" });
  await expect(tape).toBeVisible();
  // Клик далеко правее открытой границы прижимается к ней: на уровне 1 это 0,5 с.
  const box = (await tape.boundingBox())!;
  await page.mouse.click(box.x + box.width - 2, box.y + box.height / 2);
  await expect(tape).toHaveAttribute("aria-valuenow", "0.5");
  await expect(tape).toHaveAttribute("aria-valuemax", "0.5");
});
