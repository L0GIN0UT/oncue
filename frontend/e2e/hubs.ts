/** Помощники e2e для экранов-хабов (соло, онлайн, сборники). */
import type { Locator, Page } from "@playwright/test";

const TAB_BY_SOURCE: Record<string, string> = {
  random: "Быстрые",
  marathon: "Марафоны",
  pair: "Пары",
};

/**
 * Открыть `/solo` и раскрыть строку режима `<домен>.<тип>.<имя>`: домен — переключатель,
 * тип — фильтр-вкладка. Возвращает строку (`mode-card-<key>`).
 */
export async function openSoloMode(page: Page, key: string): Promise<Locator> {
  const [domain, source] = key.split(".");
  await page.goto("/solo");
  await page.getByRole("radio", { name: domain === "film" ? "Кино" : "Музыка" }).click();
  await page.getByRole("tab", { name: TAB_BY_SOURCE[source!]! }).click();
  const row = page.getByTestId(`mode-card-${key}`);
  const head = row.getByRole("button", { expanded: false });
  if (await head.count()) await head.click();
  return row;
}

/** Раздел рейтинга в блоке дуэли хаба: переключатель «Музыка / Кино». */
export async function pickLadder(page: Page, ladder: "music" | "film"): Promise<void> {
  await page.getByRole("radio", { name: ladder === "film" ? "Кино" : "Музыка" }).click();
}

/** Создать комнату по режиму из хаба: раскрыть «Создать комнату» и выбрать режим. */
export async function createRoomFromHub(page: Page, modeKey: string): Promise<void> {
  const row = page.getByTestId(`create-room-${modeKey}`);
  if (!(await row.isVisible())) await page.getByTestId("create-room-toggle").click();
  await row.click();
}
