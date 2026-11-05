/**
 * Страницы-каталоги на узком экране: контент не шире окна, горизонтальной прокрутки нет
 * (одноколоночная сетка без явной колонки раздувалась по самому широкому ребёнку).
 */
import { expect, login, test } from "./fixtures";

for (const path of ["/collections", "/lobbies"]) {
  for (const width of [375, 320]) {
    test(`${path} на ${width} px без горизонтальной прокрутки`, async ({ page }) => {
      await page.setViewportSize({ width, height: 800 });
      await login(page);
      await page.goto(path);
      await expect(page.locator("main")).toBeVisible();
      await expect
        .poll(() =>
          page.evaluate(
            () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
          ),
        )
        .toBeLessThanOrEqual(0);
    });
  }
}
