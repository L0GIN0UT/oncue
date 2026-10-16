/**
 * Конструктор сборника на моках (этап 3b, D2): создание, поиск и добавление единиц,
 * автосохранение, ошибки публикации по полям, конфликт двух вкладок (два входа = два
 * устройства) с выбором «моя версия» и публикация. Селекторы — контракт `../e2e/collections.spec.ts`.
 */
import type { Page } from "@playwright/test";
import { expect, login, test } from "./fixtures";

const FILMS = [
  { id: "00000000-0000-4000-8000-000000000201", q: "Брат", title: "Брат" },
  { id: "00000000-0000-4000-8000-000000000203", q: "Ирония", title: "Ирония судьбы" },
  { id: "00000000-0000-4000-8000-000000000204", q: "Солярис", title: "Солярис" },
  { id: "00000000-0000-4000-8000-000000000205", q: "Сталкер", title: "Сталкер" },
  { id: "00000000-0000-4000-8000-000000000206", q: "Ночной", title: "Ночной дозор" },
  { id: "00000000-0000-4000-8000-000000000207", q: "Девятая", title: "Девятая рота" },
];

const isDraftSave = (r: { url(): string; request(): { method(): string } }) =>
  r.request().method() === "PUT" &&
  /\/api\/collections\/v1\/collections\/[^/]+\/draft$/.test(r.url());

async function addItem(page: Page, f: (typeof FILMS)[number]) {
  await page.getByTestId("editor-item-search").fill(f.q);
  await page.getByTestId(`editor-add-${f.id}`).click();
  await expect(page.getByTestId(`editor-item-${f.id}`)).toContainText(f.title);
}

test("новый сборник: ошибки публикации, автосохранение, публикация", async ({
  page,
  expectAccessible,
}) => {
  test.setTimeout(60_000);
  await login(page);
  await page.goto("/collections/new");
  await page.getByTestId("new-title").fill("Моё кино");
  await page.getByRole("button", { name: "Создать черновик" }).click();
  await expect(page).toHaveURL(/\/collections\/[^/]+\/edit$/);
  await expect(page.getByTestId("editor-title")).toHaveValue("Моё кино");

  // Две единицы и без режима — публикация отклонена с ошибками по полям.
  await addItem(page, FILMS[0]!);
  await addItem(page, FILMS[1]!);
  await page.getByTestId("editor-publish").click();
  const errors = page.getByTestId("editor-errors");
  await expect(errors).toContainText("Слишком мало единиц: нужно не меньше 5");
  await expect(errors).toContainText("Выберите режим сборника");
  await expectAccessible(page);

  for (const f of FILMS.slice(2, 5)) await addItem(page, f);
  await page.getByTestId("editor-mode").selectOption("film.collection.frame");
  await expect(page.getByTestId(`editor-playable-${FILMS[0]!.id}`)).toHaveAttribute(
    "data-playable",
    "true",
  );
  await expect(page.getByTestId("editor-save-status")).toHaveText("Черновик сохранён", {
    timeout: 10_000,
  });

  const [published] = await Promise.all([
    page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/publish")),
    page.getByTestId("editor-publish").click(),
  ]);
  expect(published.status()).toBe(200);
  await expect(page).toHaveURL(/\/collections\/[^/]+$/);
  await expect(page.getByRole("heading", { name: "Моё кино" })).toBeVisible();
  await expect(page.getByTestId("collection-items-count")).toHaveText("5");
});

test("две вкладки: вторая получает 409 и экран выбора, «моя версия», публикация", async ({
  page,
  browser,
  expectAccessible,
}) => {
  test.setTimeout(60_000);
  await login(page);
  await page.goto("/collections/new");
  await page.getByTestId("new-title").fill("Конфликт");
  await page.getByRole("button", { name: "Создать черновик" }).click();
  await expect(page).toHaveURL(/\/edit$/);
  for (const f of FILMS.slice(0, 5)) await addItem(page, f);
  await page.getByTestId("editor-mode").selectOption("film.collection.frame");
  await expect(page.getByTestId("editor-save-status")).toHaveText("Черновик сохранён", {
    timeout: 10_000,
  });
  const editUrl = page.url();

  const second = await browser.newContext();
  try {
    const tab2 = await second.newPage();
    await login(tab2);
    await tab2.goto(editUrl);
    await expect(tab2.getByTestId("editor-title")).toHaveValue("Конфликт");

    // Вкладка 1 меняет название — сохраняется.
    const [saved1] = await Promise.all([
      page.waitForResponse(isDraftSave),
      page.getByTestId("editor-title").fill("Конфликт (вкладка 1)"),
    ]);
    expect(saved1.status()).toBe(200);

    // Вкладка 2 на старой ревизии добавляет шестую единицу — 409 и экран выбора.
    await tab2.getByTestId("editor-item-search").fill(FILMS[5]!.q);
    const [conflict] = await Promise.all([
      tab2.waitForResponse(isDraftSave),
      tab2.getByTestId(`editor-add-${FILMS[5]!.id}`).click(),
    ]);
    expect(conflict.status()).toBe(409);
    const screen = tab2.getByTestId("draft-conflict");
    await expect(screen).toBeVisible();
    await expect(screen).toContainText("Конфликт (вкладка 1)");
    await expectAccessible(tab2);

    const [kept] = await Promise.all([
      tab2.waitForResponse(isDraftSave),
      tab2.getByTestId("draft-keep-mine").click(),
    ]);
    expect(kept.status()).toBe(200);
    await expect(screen).toBeHidden();

    const [published] = await Promise.all([
      tab2.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/publish")),
      tab2.getByTestId("editor-publish").click(),
    ]);
    expect(published.status()).toBe(200);
    await expect(tab2.getByRole("heading", { name: "Конфликт" })).toBeVisible();
    await expect(tab2.getByTestId("collection-items-count")).toHaveText("6");
  } finally {
    await second.close();
  }
});
