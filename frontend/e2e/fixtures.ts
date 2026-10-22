/** Общие фикстуры e2e на моках: сброс мок-бэкенда, управление сценариями, проверка axe. */
import AxeBuilder from "@axe-core/playwright";
import {
  test as base,
  expect,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";
import type { Scenario } from "./mocks/world";
import type { UgcScenario } from "./mocks/ugc";
import type { MatchScenario } from "./mocks/match";
import { installFakeRealtime } from "./realtime";

const MOCK_URL = `http://127.0.0.1:${process.env.E2E_MOCK_PORT ?? 3101}`;

type Fixtures = {
  scenario: (s: Scenario & { ugc?: UgcScenario; match?: MatchScenario }) => Promise<void>;
  /** Ещё один игрок онлайна: свой контекст браузера (cookie), вход под `nickname`. */
  player: (nickname: string) => Promise<Page>;
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
    await installFakeRealtime(page);
    await use(page);
  },
  player: async ({ browser, baseURL, locale, timezoneId }, use) => {
    const contexts: BrowserContext[] = [];
    await use(async (nickname) => {
      const context = await browser.newContext({ baseURL, locale, timezoneId });
      contexts.push(context);
      await installFakeRealtime(context);
      const page = await context.newPage();
      await login(page, nickname);
      return page;
    });
    for (const c of contexts) await c.close();
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

/** Ход клиента: POST ответа, пропуска или сдачи (как в ../e2e/modes.spec.ts). */
export function isMove(url: string, method: string): boolean {
  return (
    method === "POST" &&
    /\/api\/game\/v1\/sessions\/[^/]+\/(guesses|skips|rounds\/\d+\/give-up)$/.test(url)
  );
}

/**
 * Довести сессию до итога: «Дальше»/«К итогам» (`reveal-next`), иначе — действие раунда
 * (`give-up` или первый `pick-option-*`). Каждый ход ждёт ответ сервера.
 */
export async function playUntilResults(page: Page, action: Locator, maxSteps = 80) {
  const next = page.getByTestId("reveal-next");
  const done = page.getByTestId("final-score");
  for (let step = 0; step < maxSteps; step++) {
    await expect(done.or(next).or(action).first()).toBeVisible();
    if (await done.isVisible()) return;
    if (await next.isVisible()) {
      await next.click();
      continue;
    }
    await Promise.all([
      page.waitForResponse((r) => isMove(r.url(), r.request().method())),
      action.first().click(),
    ]);
  }
  throw new Error(`сессия не дошла до итога за ${maxSteps} шагов`);
}

export const giveUpButton = (page: Page) => page.locator("[data-testid=give-up]:enabled");
export const pickButton = (page: Page) => page.locator("[data-testid^=pick-option-]:enabled");

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

/** Войти через форму (мок identity: `player` и `author`, пароль `correct-horse`). */
export async function login(page: Page, nickname = "player"): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("E-mail или ник").fill(nickname);
  await page.getByLabel("Пароль").fill("correct-horse");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByTestId("user-name")).toHaveText(nickname);
}

/** Мутация API от имени страницы (cookie + CSRF + Origin + Idempotency-Key), как у клиента. */
export async function apiPut(page: Page, path: string, data?: unknown) {
  const csrf = (await page.context().cookies()).find((c) => c.name === "gv_csrf")?.value ?? "";
  return page.request.put(path, {
    data,
    headers: {
      "X-CSRF-Token": csrf,
      Origin: new URL(page.url()).origin,
      "Idempotency-Key": crypto.randomUUID(),
    },
  });
}
