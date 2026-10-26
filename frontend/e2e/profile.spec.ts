/**
 * Профиль и прогресс на моках (этап 5a): приглашение гостю, профиль пользователя, спин колеса
 * (результат без перезагрузки, затем «уже крутили»), деградация блока и карточка на главной.
 * Против живых сервисов — `../e2e/progression.spec.ts` (шаг 0b).
 */
import { expect, login, test, openMenuItem } from "./fixtures";

test("гость: профиль предлагает завести аккаунт, пункта «Профиль» в шапке нет", async ({
  page,
}) => {
  await page.goto("/");
  // Гостю плитка прогресса зовёт войти.
  await expect(page.getByTestId("home-progress")).toHaveAttribute("href", "/login");
  await page.goto("/profile");
  const gate = page.getByTestId("profile-gate");
  await expect(gate).toBeVisible();
  await expect(gate.getByRole("link", { name: /Войти|Зарегистрироваться/ })).toBeVisible();
  await expect(page.getByTestId("nav-profile")).toHaveCount(0);
});

test("пользователь: профиль показывает уровень, Версы, задания, бейджи и колесо", async ({
  page,
  expectAccessible,
}) => {
  await login(page);
  await openMenuItem(page, "nav-profile");
  await expect(page).toHaveURL(/\/profile$/);

  await expect(page.getByTestId("level-value")).toHaveText(/Уровень 7/);
  await expect(page.getByTestId("xp-bar")).toHaveAttribute("aria-valuenow", "24");
  await expect(page.getByTestId("streaks")).toContainText("5 подряд");
  await expect(page.getByTestId("wallet-total")).toContainText("40");
  await expect(page.getByTestId("quest-play_three")).toHaveAttribute("data-completed", "true");
  await expect(page.getByTestId("badge-first_win")).toHaveAttribute("data-unlocked", "true");
  await expect(page.getByTestId("sector-verses_5")).toContainText("30%");
  await expectAccessible(page);
});

test("колесо: спин меняет состояние на «уже крутили» без перезагрузки", async ({
  page,
  expectAccessible,
}) => {
  await login(page);
  await page.goto("/profile");
  const spin = page.getByTestId("wheel-spin");
  await expect(spin).toBeEnabled();

  await spin.click();

  await expect(page.getByTestId("wheel-result")).toContainText("Выпало: 5 Версов");
  await expect(page.getByTestId("sector-verses_5")).toHaveAttribute("data-hit", "true");
  await expect(spin).toBeDisabled();
  await expect(page.getByTestId("wheel-spun")).toContainText("уже крутили");
  await expectAccessible(page);

  // После перезагрузки состояние то же: результат дня берётся из данных профиля.
  await page.reload();
  await expect(page.getByTestId("wheel-spin")).toBeDisabled();
  await expect(page.getByTestId("wheel-result")).toContainText("Выпало: 5 Версов");
});

test("колесо: 409 и повтор из другой вкладки", async ({ page, scenario }) => {
  await login(page);
  await scenario({ spinConflict: true });
  await page.goto("/profile");
  await page.getByTestId("wheel-spin").click();
  await expect(page.getByTestId("wheel-error")).toContainText("недоступно");
  await expect(page.getByTestId("wheel-spin")).toBeEnabled();

  await scenario({ spinConflict: false, spunElsewhere: true });
  await page.getByTestId("wheel-spin").click();
  await expect(page.getByTestId("wheel-result")).toContainText("Сегодня вы уже крутили");
});

test("профиль игрока: уровень и бейджи; неизвестный игрок — сообщение; гость — приглашение", async ({
  page,
  expectAccessible,
}) => {
  await page.goto("/players/00000000-0000-4000-8000-000000000902");
  await expect(page.getByTestId("profile-gate")).toBeVisible();

  await login(page);
  await page.goto("/players/00000000-0000-4000-8000-000000000902");
  await expect(page.getByTestId("player-level")).toHaveText(/Уровень 12/);
  await expect(page.getByTestId("player-badge-first_win")).toContainText("Первая победа");
  // Ник из social вместо «Игрок» и жалоба на чужую аватарку (ADR 0069).
  await expect(page.getByTestId("player-nickname")).toHaveText("Мастер");
  await page.getByTestId("report-avatar").click();
  await expect(page.getByRole("dialog")).toContainText("Жалоба на аватарку");
  await page.keyboard.press("Escape");
  await expectAccessible(page);

  await page.goto("/players/00000000-0000-4000-8000-0000000009ff");
  await expect(page.getByTestId("player-error")).toContainText("Такого игрока нет");
});

test("деградация: погасший блок не роняет страницу", async ({ page, scenario }) => {
  await login(page);
  await scenario({ progressDown: ["wallet", "wheel"] });
  await page.goto("/profile");

  await expect(page.getByTestId("block-wallet-unavailable")).toContainText("Появится позже");
  await expect(page.getByTestId("block-wheel-unavailable")).toBeVisible();
  await expect(page.getByTestId("level-value")).toBeVisible();
  await expect(page.getByTestId("quest-win_duel")).toBeVisible();
});

test("главная: прогресс и ранги из bff, заглушка при сбое", async ({ page, scenario }) => {
  await login(page);
  await page.goto("/");
  const progress = page.getByTestId("home-progress");
  await expect(progress).toContainText("Уровень");
  await expect(progress).toContainText("серия");
  await expect(page.getByTestId("home-rank-music")).toBeVisible();
  await expect(page.getByTestId("home-rank-film")).toBeVisible();

  await scenario({ progressDown: ["home"] });
  await page.reload();
  await expect(page.getByTestId("home-progress")).toContainText("Прогресс сейчас недоступен");
  await expect(page.getByTestId("nav-profile")).toBeVisible();
});
