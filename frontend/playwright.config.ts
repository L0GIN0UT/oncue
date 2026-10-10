/**
 * Playwright на моках: `next build && next start` + мок-бэкенд по контрактам (e2e/mocks/server.ts).
 * Браузер — Chromium из PLAYWRIGHT_BROWSERS_PATH (в CI и dev-окружении предустановлен).
 * Полный стек на compose — ../e2e (только CI).
 */
import { defineConfig, devices } from "@playwright/test";

const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3100);
const MOCK_PORT = Number(process.env.E2E_MOCK_PORT ?? 3101);
export const MOCK_URL = `http://127.0.0.1:${MOCK_PORT}`;

export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  timeout: 45_000,
  expect: { timeout: 7_000 },
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
  webServer: [
    {
      command: "tsx e2e/mocks/server.ts",
      url: `${MOCK_URL}/healthz`,
      env: { MOCK_PORT: String(MOCK_PORT) },
      reuseExistingServer: !process.env.CI,
      stdout: "pipe",
    },
    {
      command: `next start -p ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}/healthz`,
      env: {
        GV_DEV_API_ORIGIN: MOCK_URL,
        GV_BFF_INTERNAL_URL: MOCK_URL,
        NEXT_TELEMETRY_DISABLED: "1",
      },
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
