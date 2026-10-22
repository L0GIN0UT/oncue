/**
 * Общие шаги e2e онлайна этапа 4a (`lobby.spec.ts`, `duel.spec.ts`, `party.spec.ts`) на
 * полном стеке: регистрация игроков, вход в комнату, ходы по UI, статусы игроков.
 *
 * Игроки — свежие пользователи каждого прогона (как в `collections.spec.ts`): регистрация
 * через UI, код подтверждения e-mail — из лога identity (`docker compose logs identity`,
 * в local/ci письма пишутся в лог). Засеянные постоянные пользователи не подходят: комната,
 * брошенная упавшим прогоном, держала бы их (409 `match.active_room_conflict`).
 *
 * Ответы тесту неизвестны (план раундов живёт только в снимке комнаты match): уровни
 * проходятся пропуском (`match-skip`) или заведомо неверным ответом. Ожиданий по времени
 * нет — только по состояниям UI; таймауты учитывают окна уровней шаблонов (≤ 30 с).
 */
import { randomUUID } from "node:crypto";
import {
  expect,
  test,
  type APIResponse,
  type Browser,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";
import { registerViaUi } from "./auth";

/** Уникальный хвост прогона: ники и тексты не пересекаются с прошлыми прогонами на стеке. */
export const RUN = randomUUID().slice(0, 8);

/** Ответы API с ошибкой (≥ 400) по браузерному контексту — в текст падения шага (диагностика стека). */
const apiErrors = new WeakMap<BrowserContext, string[]>();

function trackApiErrors(context: BrowserContext, nickname: string): void {
  const log: string[] = [];
  apiErrors.set(context, log);
  context.on("response", (res) => {
    const url = new URL(res.url());
    if (res.status() < 400 || !url.pathname.startsWith("/api/")) return;
    void res
      .text()
      .catch(() => "")
      .then((body) => {
        log.push(
          `${new Date().toISOString()} ${nickname} ${res.status()} ${res.request().method()} ${url.pathname} ${body.slice(0, 300)}`,
        );
        if (log.length > 20) log.shift();
      });
  });
}

/** Последние ошибки API игроков — для сообщения о падении. */
export function apiErrorsOf(pages: Page[]): string {
  const lines = pages.flatMap((p) => apiErrors.get(p.context()) ?? []);
  return lines.length
    ? `\nОшибки API:\n${lines.sort().slice(-30).join("\n")}`
    : "\nОшибок API не было";
}

async function withApiErrors<T>(
  pages: Page[],
  step: () => Promise<T>,
): Promise<T> {
  try {
    return await step();
  } catch (error) {
    if (error instanceof Error) error.message += apiErrorsOf(pages);
    throw error;
  }
}
const PASSWORD = "e2e-online-password-1";

/** Самое длинное окно уровня онлайн-шаблонов засева + запас на доставку дельты (мс). */
export const LEVEL_WINDOW_MS = 30_000 + 10_000;
/**
 * Сколько ждать «Отключён» у соперников: `GV_MATCH__PRESENCE_TIMEOUT_S` (12 с) после
 * последнего tick (`GV_MATCH__TICK_S`, 5 с) + цикл владельца и доставка дельты.
 */
export const DISCONNECT_MS = 12_000 + 5_000 + 13_000;

export type Player = { context: BrowserContext; page: Page; nickname: string };
type Problem = { code: string; details?: Record<string, unknown> | null };

// ----------------------------------------------------------------------------- регистрация

/** Регистрация через UI → код из письма (лог identity) → сессия. Свой контекст на игрока. */
export async function registerPlayer(
  browser: Browser,
  prefix: string,
): Promise<Player> {
  const nickname = `${prefix}${RUN}`;
  const context = await browser.newContext();
  trackApiErrors(context, nickname);
  const page = await context.newPage();
  await registerViaUi(page, nickname, PASSWORD);
  return { context, page, nickname };
}

/** Игроки по префиксам — последовательно (общий лимит gateway на регистрацию с одного IP). */
export async function registerPlayers(
  browser: Browser,
  prefixes: string[],
): Promise<Player[]> {
  const players: Player[] = [];
  for (const prefix of prefixes)
    players.push(await registerPlayer(browser, prefix));
  return players;
}

export async function closePlayers(players: Player[]): Promise<void> {
  for (const p of players) await p.context.close().catch(() => undefined);
}

// ----------------------------------------------------------------------------- API от имени игрока
/** Публичный API сервиса через gateway от имени страницы: cookie + CSRF + Origin. */
export function api(page: Page, service: "match" | "collections" | "game") {
  const base = `/api/${service}/v1`;
  const headers = async (): Promise<Record<string, string>> => {
    const csrf =
      (await page.context().cookies()).find((c) => c.name === "gv_csrf")
        ?.value ?? "";
    // Запросы собираются руками — Origin ставим сами (cookie-мутации требуют его вместе с CSRF).
    return {
      "X-CSRF-Token": csrf,
      Origin: new URL(page.url()).origin,
      "Idempotency-Key": randomUUID(),
    };
  };
  return {
    get: (p: string) => page.request.get(`${base}${p}`),
    post: async (p: string, data?: unknown) =>
      page.request.post(`${base}${p}`, { data, headers: await headers() }),
  };
}

export async function json<T>(
  res: Pick<APIResponse, "status" | "text" | "json" | "url">,
  status = 200,
): Promise<T> {
  expect(
    res.status(),
    `${res.url()} → ${res.status()}: ${await res.text()}`,
  ).toBe(status);
  return (await res.json()) as T;
}

export async function problem(
  res: Pick<APIResponse, "status" | "text" | "json" | "url">,
  status: number,
): Promise<Problem> {
  expect(res.status(), `${res.url()}: ${await res.text()}`).toBe(status);
  return (await res.json()) as Problem;
}

// ----------------------------------------------------------------------------- комната
const CODE_URL = /\/room\/[A-HJ-NP-Z2-9]{8}$/;

export async function roomCode(page: Page): Promise<string> {
  await withApiErrors([page], () => expect(page).toHaveURL(CODE_URL));
  return (await page.getByTestId("room-code").textContent())!.trim();
}

/** Хост создаёт комнату из хаба «Онлайн» по ключу шаблона; вернуть код. */
export async function createRoomFromHub(
  page: Page,
  modeKey: string,
): Promise<string> {
  await page.goto("/online");
  await page.getByTestId(`create-room-${modeKey}`).click();
  return roomCode(page);
}

/** Вход по коду с экрана лобби (`/lobbies`): ввод строчными приводится к верхнему регистру. */
export async function joinByCode(page: Page, code: string): Promise<void> {
  await page.goto("/lobbies");
  await page.getByTestId("room-code-input").fill(code.toLowerCase());
  await expect(page.getByTestId("room-code-input")).toHaveValue(code);
  await page.getByTestId("room-code-submit").click();
  await expect(page.getByTestId("room-preview")).toBeVisible();
  await page.getByTestId("join-room").click();
  await expect(page.getByTestId("room-code")).toHaveText(code);
}

/** Вход по ссылке на комнату (`/room/CODE`). */
export async function joinByLink(page: Page, code: string): Promise<void> {
  await page.goto(`/room/${code}`);
  await expect(page.getByTestId("room-preview")).toBeVisible();
  await page.getByTestId("join-room").click();
  await expect(page.getByTestId("room-code")).toHaveText(code);
}

export const playerRow = (page: Page, nickname: string): Locator =>
  page.getByTestId(`player-${nickname}`);
export const readyState = (page: Page, nickname: string): Locator =>
  playerRow(page, nickname).getByTestId("ready-state");
export const playerStatus = (page: Page, nickname: string): Locator =>
  playerRow(page, nickname).getByTestId("player-status");

/** Все отмечают готовность; у хоста все строки — «Готов». */
export async function allReady(host: Page, players: Player[]): Promise<void> {
  for (const p of players) {
    const toggle = p.page.getByTestId("ready-toggle");
    await expect(toggle).toHaveAttribute("aria-pressed", "false");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
  }
  for (const p of players)
    await expect(readyState(host, p.nickname)).toHaveText("Готов");
}

/** Хост стартует матч: отсчёт, затем первый раунд у всех. */
export async function startMatch(
  host: Page,
  players: Player[],
  roundsTotal: number,
): Promise<void> {
  await expect(host.getByTestId("start-match")).toBeEnabled();
  await host.getByTestId("start-match").click();
  for (const p of players) {
    await expect(p.page.getByTestId("match-round")).toHaveText(
      `Раунд 1 из ${roundsTotal}`,
      { timeout: 30_000 },
    );
  }
}

// ----------------------------------------------------------------------------- ходы
/** Поле ответа матча (формат `classic`): включено, пока свой ход на открытом уровне возможен. */
export const answerInput = (page: Page): Locator =>
  page.getByTestId("match-stage").getByRole("combobox");

/** Неверный ответ свободным текстом (Enter без выбранной подсказки отправляет текст). */
export async function wrongGuess(page: Page, text: string): Promise<void> {
  const input = answerInput(page);
  await expect(input).toBeEnabled({ timeout: LEVEL_WINDOW_MS });
  const [res] = await Promise.all([
    page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        /\/api\/match\/v1\/rooms\/[^/]+\/match\/guess$/.test(r.url()),
    ),
    (async () => {
      await input.fill(text);
      await input.press("Enter");
    })(),
  ]);
  expect([200, 202], await res.text()).toContain(res.status());
}

/** Пропуск уровня (недоступен на последнем уровне раунда). */
export async function skipLevel(page: Page): Promise<void> {
  const skip = page.getByTestId("match-skip");
  await expect(skip).toBeEnabled({ timeout: LEVEL_WINDOW_MS });
  const [res] = await Promise.all([
    page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        /\/api\/match\/v1\/rooms\/[^/]+\/match\/skip$/.test(r.url()),
    ),
    skip.click(),
  ]);
  expect([200, 202], await res.text()).toContain(res.status());
}

/**
 * Сыграть свой ход на открытом уровне, если он возможен: пропуск, а на последнем уровне —
 * неверный ответ. Вернуть, был ли ход. Ждёт открытия хода не дольше `waitMs`: уровень могли
 * уже закрыть (окно, все ответили) — тогда хода нет, это не ошибка.
 */
async function passIfOpen(
  page: Page,
  text: string,
  waitMs: number,
): Promise<boolean> {
  const input = answerInput(page);
  try {
    await expect(input).toBeEnabled({ timeout: waitMs });
  } catch {
    return false;
  }
  const skip = page.getByTestId("match-skip");
  const move =
    (await skip.isVisible()) && (await skip.isEnabled()) ? "skip" : "guess";
  const response = page
    .waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        new RegExp(`/api/match/v1/rooms/[^/]+/match/${move}$`).test(r.url()),
      { timeout: 10_000 },
    )
    .catch(() => null);
  if (move === "skip") await skip.click();
  else {
    await input.fill(text);
    await input.press("Enter");
  }
  // Ответ сервера: 200/202 — ход принят; 409/422 — уровень закрылся раньше (гонка с окном).
  await response;
  return true;
}

/**
 * Проходить уровни всеми игроками (пропуск / неверный ответ), пока у первого игрока не
 * появится `done` (ревил, итог и т. п.). Без пауз: каждый шаг ждёт состояния UI —
 * открытого поля ответа у первого игрока или `done`.
 */
export async function passUntil(
  players: Player[],
  done: (page: Page) => Locator,
  maxSteps = 200,
): Promise<void> {
  const first = players[0]!.page;
  for (let step = 0; step < maxSteps; step++) {
    const target = done(first);
    await withApiErrors(
      players.map((p) => p.page),
      () =>
        expect(
          target
            .or(
              first.locator(
                "[data-testid=match-stage] input[role=combobox]:enabled",
              ),
            )
            .first(),
        ).toBeVisible({
          timeout: LEVEL_WINDOW_MS,
        }),
    );
    if (await target.first().isVisible()) return;
    for (const [i, p] of players.entries()) {
      // Первый игрок уже на открытом уровне; остальным даём догнать дельту канала.
      await passIfOpen(
        p.page,
        `мимо-${RUN}-${step}-${i}`,
        i === 0 ? 1_000 : 5_000,
      );
    }
  }
  throw new Error(`матч не дошёл до ожидаемого состояния за ${maxSteps} шагов`);
}

/** Сдаться (дуэль — поражение, party — выход с сохранением очков). */
export async function forfeit(page: Page): Promise<void> {
  await page.getByTestId("forfeit").click();
  const [res] = await Promise.all([
    page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        /\/api\/match\/v1\/rooms\/[^/]+\/match\/forfeit$/.test(r.url()),
    ),
    page.getByTestId("forfeit-confirm").click(),
  ]);
  expect([200, 202], await res.text()).toContain(res.status());
}
