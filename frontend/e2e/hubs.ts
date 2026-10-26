/** Помощники e2e для экранов-хабов (соло, онлайн, сборники). */
import type { Locator, Page } from "@playwright/test";

const TAB_BY_SOURCE: Record<string, string> = {
  random: "Быстрые",
  marathon: "Марафоны",
  pair: "Пары",
  weekly: "Сетка недели",
};

/**
 * Открыть `/solo` и раскрыть строку режима `<домен>.<тип>.<имя>`: домен — переключатель,
 * тип — вкладка. Возвращает строку (`mode-card-<key>`).
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
