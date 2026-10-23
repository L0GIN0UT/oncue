/**
 * Полный стек: прогресс 5a (план docs/plans/stage-5.md §0, §8; ADR 0042-0044).
 *
 * Сценарий: свежий аккаунт (регистрация через UI) проходит соло-партию до итога; consumer
 * progression начисляет опыт и Версы (асинхронно, поэтому опрос с таймаутом), профиль
 * показывает уровень, колесо крутится один раз в сутки (повтор возвращает тот же сектор).
 * Значения сида не зашиты: проверяются инварианты (суммы > 0, повтор идемпотентен, приз
 * отражается в кошельке/опыте).
 *
 * Что нужно для запуска: поднятый стек (`gv dev up`, dev-оверлей монтирует `seeds/progression`,
 * и relay progression публикует версию 1 конфига задачей `progression.config.bootstrap`),
 * засев каталога и режимов (`gv dev seed`) и `E2E_SEEDED=1`. Без опубликованного конфига
 * начисления ждут его (повторимая ошибка), спин отвечает ошибкой.
 */
import { expect, test, type Page } from "@playwright/test";
import { api, closePlayers, json, registerPlayer, type Player } from "./online";

test.skip(process.env.E2E_SEEDED !== "1", "стек не засеян: нужен шаг засева (E2E_SEEDED=1)");

const SOLO = "music.random.classic";
const ACCRUAL_TIMEOUT_MS = 60_000;

type Progress = {
  level: number;
  max_level: number;
  total_xp: number;
  xp_in_level: number;
  xp_to_next: number | null;
  config_version: number;
};
type Wallet = { total: number; earned: number; paid: number };
type Prize = {
  kind: "xp" | "verses" | "boost" | "item" | "none";
  amount: number;
  matches: number | null;
  multiplier: number | null;
};
type Spin = { sector_key: string; prize: Prize; repeated: boolean; day: string };
type Wheel = { enabled: boolean; sectors: { key: string }[]; today: Spin | null };

let player: Player;

test.beforeAll(async ({ browser }) => {
  player = await registerPlayer(browser, "prog");
});

test.afterAll(async () => {
  await closePlayers(player ? [player] : []);
});

/** Ход клиента: ответ, пропуск или сдача (ждём ответ сервера, чтобы не кликнуть по старой версии). */
function isMoveUrl(url: string, method: string): boolean {
  return (
    method === "POST" &&
    /\/api\/game\/v1\/sessions\/[^/]+\/(guesses|skips|rounds\/\d+\/give-up)$/.test(url)
  );
}

/** Довести открытую сессию до итога сдачей раундов (ответы тесту неизвестны). */
async function finishByGivingUp(page: Page, maxSteps = 60): Promise<void> {
  const next = page.getByTestId("reveal-next");
  const done = page.getByTestId("final-score");
  const giveUp = page.locator("[data-testid=give-up]:enabled");
  for (let step = 0; step < maxSteps; step++) {
    await expect(done.or(next).or(giveUp).first()).toBeVisible();
    if (await done.isVisible()) return;
    if (await next.isVisible()) {
      await next.click();
      continue;
    }
    await Promise.all([
      page.waitForResponse((r) => isMoveUrl(r.url(), r.request().method())),
      giveUp.first().click(),
    ]);
  }
  throw new Error(`сессия не дошла до итога за ${maxSteps} шагов`);
}

test("соло-партия начисляет опыт и Версы, профиль показывает уровень", async () => {
  const { page } = player;
  const progression = api(page, "progression");

  // До партии у нового игрока нет опыта.
  const before = await json<Progress>(await progression.get("/me/progress"));
  expect(before.total_xp).toBe(0);

  await page.goto(`/play/${SOLO}`);
  await expect(page).toHaveURL(/\/play\/s\//);
  await finishByGivingUp(page);

  // Начисление идёт через события (game → outbox → progression): ждём опросом.
  await expect
    .poll(async () => (await json<Progress>(await progression.get("/me/progress"))).total_xp, {
      timeout: ACCRUAL_TIMEOUT_MS,
      message: "опыт за соло-партию",
    })
    .toBeGreaterThan(0);
  await expect
    .poll(async () => (await json<Wallet>(await progression.get("/me/wallet"))).total, {
      timeout: ACCRUAL_TIMEOUT_MS,
      message: "Версы за соло-партию",
    })
    .toBeGreaterThanOrEqual(1);

  const after = await json<Progress>(await progression.get("/me/progress"));
  expect(after.config_version).toBeGreaterThanOrEqual(1);
  expect(after.level).toBeGreaterThanOrEqual(1);
  expect(after.level).toBeLessThanOrEqual(after.max_level);
  if (after.level < after.max_level) {
    // Уровень мог не смениться за одну партию: проверяем только инварианты полосы опыта.
    expect(after.xp_to_next).toBeGreaterThan(0);
    expect(after.xp_in_level).toBeGreaterThanOrEqual(0);
  }

  await page.goto("/profile");
  await expect(page.getByTestId("level-value")).toContainText(String(after.level));
  await expect(page.getByTestId("wallet-total")).toBeVisible();
});

test("колесо: один спин в сутки, повтор возвращает тот же сектор, приз отражается", async () => {
  const { page } = player;
  const progression = api(page, "progression");

  const view = await json<Wheel>(await progression.get("/wheel"));
  expect(view.enabled).toBe(true);
  expect(view.sectors.length).toBeGreaterThan(0);
  const spunBefore = view.today;

  const walletBefore = await json<Wallet>(await progression.get("/me/wallet"));
  const xpBefore = (await json<Progress>(await progression.get("/me/progress"))).total_xp;

  const first = await json<Spin>(await progression.post("/wheel/spin"));
  expect(view.sectors.map((s) => s.key)).toContain(first.sector_key);
  // Если сегодня уже крутили (повторный прогон), ответ — повтор прежнего результата.
  expect(first.repeated).toBe(spunBefore !== null);

  const again = await json<Spin>(await progression.post("/wheel/spin"));
  expect(again.repeated).toBe(true);
  expect(again.sector_key).toBe(first.sector_key);
  expect(again.prize).toEqual(first.prize);

  const today = (await json<Wheel>(await progression.get("/wheel"))).today;
  expect(today?.sector_key).toBe(first.sector_key);

  // Приз Версов/опыта виден в кошельке/прогрессе (буст, косметика и «ничего» — без изменений).
  const walletAfter = await json<Wallet>(await progression.get("/me/wallet"));
  const xpAfter = (await json<Progress>(await progression.get("/me/progress"))).total_xp;
  if (!first.repeated && first.prize.kind === "verses") {
    expect(walletAfter.total - walletBefore.total).toBe(first.prize.amount);
  }
  if (!first.repeated && first.prize.kind === "xp") {
    expect(xpAfter - xpBefore).toBeGreaterThanOrEqual(first.prize.amount);
  }
  expect(walletAfter.total).toBeGreaterThanOrEqual(walletBefore.total);
});
