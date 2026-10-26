import { expect, giveUpButton, playUntilResults, test } from "./fixtures";
import { openSoloMode } from "./hubs";

test("витрина /solo: режимы по вкладкам, сетка недели отдельной вкладкой", async ({
  page,
  expectAccessible,
}) => {
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
  await page.getByRole("tab", { name: "Сетка недели" }).click();
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
