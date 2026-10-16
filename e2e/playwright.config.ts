/**
 * E2E на полном стеке (compose + gateway), только в CI (job e2e-smoke).
 * Запуск: `pnpm -C frontend test:e2e:stack` при поднятом стеке; адрес — E2E_BASE_URL.
 * stack.spec.ts — смоук без данных; daily.spec.ts — сценарий «дейлик», modes.spec.ts — режимы
 * этапа 3a; обоим нужен засеянный стек (`gv dev seed`: seeds/catalog, seeds/media, сиды
 * режимов) и E2E_SEEDED=1, см. план этапа 2 §0 и этапа 3a §10. Типы: `pnpm -C frontend exec
 * tsc -p ../e2e`.
 */
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts$/,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]] : "list",
  outputDir: "test-results",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.E2E_BASE_URL ?? "https://localhost",
    // Самоподписанный сертификат gateway в CI.
    ignoreHTTPSErrors: true,
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
    trace: "retain-on-failure",
  },
});
