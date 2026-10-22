/**
 * Смоук полного стека (compose + gateway): то, что не проверить моками.
 * Стек поднялся, gateway отдаёт веб-клиент по HTTPS и проксирует публичные API сервисов,
 * внутренние и служебные пути наружу закрыты, гостевая сессия выдаётся (cookie + CSRF).
 * Данные не нужны — тест работает и на пустом (незасеянном) стеке.
 */
import { expect, test } from "@playwright/test";

test("главная страница отдаётся через gateway", async ({ page }) => {
  const response = await page.goto("/");
  expect(response?.status()).toBe(200);
  await expect(page).toHaveTitle(/OnCue/);
});

test("публичные API сервисов отвечают через gateway", async ({ request }) => {
  for (const path of ["/api/catalog/v1/meta", "/api/game/v1/meta", "/api/web/v1/meta"]) {
    const res = await request.get(path);
    expect(res.status(), path).toBe(200);
  }
  const jwks = await request.get("/api/identity/.well-known/jwks.json");
  expect(jwks.status()).toBe(200);
  expect(((await jwks.json()) as { keys: unknown[] }).keys.length).toBeGreaterThan(0);
});

test("внутренние и служебные пути сервисов наружу закрыты", async ({ request }) => {
  for (const path of [
    "/api/identity/internal/v1/service-tokens",
    "/api/catalog/admin/v1/items",
    "/api/game/v1/health/ready",
    "/api/game/metrics",
  ]) {
    const res = await request.get(path);
    // gateway закрывает такие пути локацией `return 404` (infra/nginx/generated/routes.conf).
    expect(res.status(), path).toBe(404);
  }
});

test("гость получает сессию: cookie доступа и CSRF", async ({ page }) => {
  await page.goto("/");
  const origin = new URL(page.url()).origin;
  const res = await page.request.post("/api/identity/v1/auth/guest", { headers: { Origin: origin } });
  expect([204, 409]).toContain(res.status()); // 409 — клиент уже завёл гостя при загрузке
  const names = (await page.context().cookies()).map((c) => c.name);
  expect(names).toContain("gv_csrf");
});

test("онлайн только с аккаунтом: гостю — экран регистрации, API match — 403", async ({ page }) => {
  // Аноним и гость на /online и по ссылке на комнату видят экран «Онлайн доступен после
  // регистрации» с возвратом по next (план этапа 4 §0 п. 8а, D13). Данных не нужно.
  for (const path of ["/online", "/room/K7MQ2XWP"]) {
    await page.goto(path);
    await expect(page.getByTestId("online-auth-required")).toBeVisible();
  }
  const origin = new URL(page.url()).origin;
  const guest = await page.request.post("/api/identity/v1/auth/guest", { headers: { Origin: origin } });
  expect([204, 409]).toContain(guest.status());
  await page.goto("/online");
  await expect(page.getByTestId("online-auth-required")).toContainText("Онлайн доступен после регистрации");
  await expect(page.getByTestId("online-login")).toHaveAttribute("href", "/login?next=%2Fonline");
  await expect(page.getByTestId("online-register")).toHaveAttribute("href", "/register?next=%2Fonline");
  const res = await page.request.get("/api/match/v1/rooms/open");
  expect(res.status()).toBe(403);
  expect(((await res.json()) as { code: string }).code).toBe("auth.guest_forbidden");
});
