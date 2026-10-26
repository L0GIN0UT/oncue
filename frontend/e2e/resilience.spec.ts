import { expect, test } from "./fixtures";

test("409 game.desync: экран применяет актуальный снимок и продолжает игру", async ({
  page,
  scenario,
}) => {
  await page.goto("/play/music.daily.classic");
  await expect(page.getByTestId("clue-audio")).toHaveAttribute("src", /^blob:/);
  await scenario({ desyncOnce: true });
  await page.getByTestId("skip").click();
  await expect(page.getByText("Игра обновилась в другой вкладке")).toBeVisible();
  // Ход не применился, уровень прежний; следующий ход идёт с актуальной версией.
  await expect(page.getByTestId("skip")).toHaveText(/\+0,5 с/);
  await page.getByTestId("skip").click();
  await expect(page.getByTestId("skip")).toHaveText(/\+1 с/);
});

test("дейлики не готовы — понятный экран с повтором", async ({ page, scenario }) => {
  await scenario({ dailyNotReady: true });
  await page.goto("/play/film.daily.frame");
  await expect(page.getByRole("heading", { name: "Загадки дня ещё готовятся" })).toBeVisible();
  await scenario({ dailyNotReady: false });
  await page.getByRole("button", { name: "Попробовать ещё раз" }).click();
  await expect(page).toHaveURL(/\/play\/s\//);
});

test("блок дейликов недоступен — главная работает", async ({ page, scenario }) => {
  await scenario({ dailiesUnavailable: true });
  await page.goto("/");
  await expect(page.getByTestId("home-daily-music")).toContainText("сейчас недоступен");
  await expect(page.getByTestId("home-daily-film")).toContainText("сейчас недоступен");
  // Шапка и остальные входы работают, даже когда дейликов нет.
  await expect(page.getByTestId("nav-solo")).toBeVisible();
  await expect(page.getByTestId("home-solo")).toBeVisible();
});

test("ответ страницы несёт CSP с nonce", async ({ page }) => {
  const response = await page.goto("/");
  const csp = response!.headers()["content-security-policy"] ?? "";
  expect(csp).toMatch(/script-src 'self' 'nonce-[^']+' 'strict-dynamic'/);
  expect(csp).toContain("frame-ancestors 'none'");
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" && /Content Security Policy/i.test(m.text())) errors.push(m.text());
  });
  await page.reload();
  await expect(page.getByRole("navigation", { name: "Разделы" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("cookie access истекла раньше сессии: refresh и та же игра, без гостевой сессии", async ({
  page,
  context,
}) => {
  await page.goto("/play/music.daily.classic");
  await expect(page.getByTestId("clue-audio")).toHaveAttribute("src", /^blob:/);
  const sessionUrl = page.url();
  const authCalls: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/api/identity/v1/auth/")) authCalls.push(new URL(r.url()).pathname);
  });
  // Браузер удалил gv_access (истёк Max-Age), gv_refresh и gv_csrf остались.
  const cookies = await context.cookies();
  await context.clearCookies({ name: "gv_access" });
  expect(cookies.some((c) => c.name === "gv_refresh")).toBe(true);

  await page.getByTestId("skip").click();
  await expect(page.getByTestId("skip")).toHaveText(/\+1 с/);
  expect(page.url()).toBe(sessionUrl);
  expect(authCalls).toEqual(["/api/identity/v1/auth/refresh"]);
  expect((await context.cookies()).some((c) => c.name === "gv_access")).toBe(true);
});
