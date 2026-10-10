/** Общие фикстуры e2e на моках: сброс мок-бэкенда, управление сценариями, проверка axe. */
import AxeBuilder from "@axe-core/playwright";
import { test as base, expect, type Page } from "@playwright/test";
import type { Scenario } from "./mocks/world";

const MOCK_URL = `http://127.0.0.1:${process.env.E2E_MOCK_PORT ?? 3101}`;

type Fixtures = {
  scenario: (s: Scenario) => Promise<void>;
  expectAccessible: (page: Page) => Promise<void>;
};

export const test = base.extend<Fixtures>({
  page: async ({ page, request }, use) => {
    await request.post(`${MOCK_URL}/__mock/reset`);
    // Web Share API в headless-Chromium может отсутствовать или зависать — проверяем путь
    // через буфер обмена.
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "share", { value: undefined, configurable: true });
    });
    await use(page);
  },
  scenario: async ({ request }, use) => {
    await use(async (s) => {
      await request.post(`${MOCK_URL}/__mock/scenario`, { data: s });
    });
  },
  expectAccessible: async ({}, use) => {
    await use(async (page) => {
      const { violations } = await new AxeBuilder({ page }).analyze();
      const serious = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(
        serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
      ).toEqual([]);
    });
  },
});

export { expect };

/** Ответить через автокомплит: ввести префикс, выбрать подсказку, отправить. */
export async function answerWithSuggestion(
  page: Page,
  prefix: string,
  title: string,
): Promise<void> {
  const input = page.getByRole("combobox");
  await input.fill(prefix);
  await page
    .getByRole("option")
    .filter({ has: page.getByText(title, { exact: true }) })
    .click();
  await expect(input).toHaveValue(title);
  await page.getByRole("button", { name: "Ответить" }).click();
}

/** Сдать все оставшиеся раунды (быстрое завершение сессии). */
export async function giveUpRemaining(page: Page): Promise<void> {
  for (;;) {
    const toResults = page.getByRole("button", { name: "К итогам" });
    const next = page.getByRole("button", { name: "Следующий раунд" });
    const giveUp = page.locator("[data-testid=give-up]:enabled");
    await expect(toResults.or(next).or(giveUp).first()).toBeVisible();
    if (await toResults.isVisible()) {
      await toResults.click();
      return;
    }
    if (await next.isVisible()) {
      await next.click();
      continue;
    }
    await giveUp.click();
  }
}
