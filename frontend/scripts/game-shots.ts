/**
 * Снимки экранов зоны «игра» на моках: каждое состояние (раунд, ответ раунда, итоги, кино, дуэль,
 * комната, модалка дневника) на 1280×900 и 390×844 → `docs/handoff/shots/<имя>-<ширина>.png`.
 * Состояния достижимы только игрой (нужны ходы, второй игрок), поэтому их ведёт скрипт, а не
 * список маршрутов `shots.ts`. Нужна готовая сборка (`pnpm build`). Запуск:
 * `MSYS_NO_PATHCONV=1 npx tsx scripts/game-shots.ts [имя…]` (имена: см. STATES).
 */
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { chromium, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { installFakeRealtime } from "../e2e/realtime";

const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3100);
const MOCK_PORT = Number(process.env.E2E_MOCK_PORT ?? 3101);
const WEB = `http://localhost:${WEB_PORT}`;
const MOCK = `http://127.0.0.1:${MOCK_PORT}`;
const OUT = path.resolve(import.meta.dirname, "../../docs/handoff/shots");
const VIEWPORTS = [
  { width: 1280, height: 900 },
  { width: 390, height: 844 },
] as const;

function start(command: string, args: string[], env: Record<string, string>): ChildProcess {
  return spawn(command, args, {
    env: { ...process.env, ...env },
    stdio: "inherit",
    shell: process.platform === "win32",
  });
}

function stop(child: ChildProcess): void {
  if (process.platform === "win32" && child.pid)
    spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"]);
  else child.kill();
}

async function waitFor(url: string, tries = 60): Promise<void> {
  for (let i = 0; i < tries; i++) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {
      /* сервер ещё поднимается */
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error(`не дождались ${url}`);
}

async function resetMock(scenario: object = { event: "active" }): Promise<void> {
  await fetch(`${MOCK}/__mock/reset`, { method: "POST" });
  await fetch(`${MOCK}/__mock/scenario`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(scenario),
  });
}

async function newContext(
  browser: Browser,
  viewport: { width: number; height: number },
): Promise<BrowserContext> {
  const context = await browser.newContext({
    baseURL: WEB,
    viewport,
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
    reducedMotion: "reduce",
  });
  // tsx (esbuild) оборачивает вложенные функции в __name: в странице этого помощника нет.
  await context.addInitScript("window.__name = (f) => f;");
  await installFakeRealtime(context);
  context.on("page", (p) => p.on("pageerror", (e) => console.error("pageerror:", e.message)));
  return context;
}

async function login(page: Page, nickname: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Почта или ник").fill(nickname);
  await page.getByLabel("Пароль").fill("correct-horse");
  await page.getByRole("button", { name: "Войти" }).click();
  await page.getByTestId("user-name").waitFor({ state: "attached" });
}

async function shot(page: Page, name: string, width: number, fullPage = true): Promise<void> {
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(400);
  const file = path.join(OUT, `${name}-${width}.png`);
  await page.screenshot({ path: file, fullPage });
  console.log("снимок:", path.relative(process.cwd(), file));
}

async function answer(page: Page, text: string): Promise<void> {
  const input = page.getByRole("combobox");
  await input.waitFor();
  await input.fill(text);
  await input.press("Enter");
}

type Ctx = { browser: Browser; viewport: (typeof VIEWPORTS)[number] };

/** Дейлик музыки: промах, пропуск, верный ответ на третьей попытке, итоги. */
async function music({ browser, viewport }: Ctx): Promise<void> {
  await resetMock();
  const context = await newContext(browser, viewport);
  const page = await context.newPage();
  await login(page, "player");
  await page.goto("/play/music.daily.classic");
  await page.getByTestId("clue-audio").waitFor({ state: "attached" });
  await shot(page, "game-start", viewport.width);
  await answer(page, "совсем не то");
  await page.getByTestId("skip").waitFor();
  await page.getByTestId("skip").click();
  await page.getByRole("button", { name: "Слушать 2 с" }).waitFor();
  await shot(page, "game-round", viewport.width);
  await page.getByRole("combobox").fill("Групп");
  await page
    .getByRole("option")
    .filter({ has: page.getByText("Группа крови", { exact: true }) })
    .click();
  await shot(page, "game-answer", viewport.width);
  await page.getByRole("button", { name: "Ответить" }).click();
  await page.getByTestId("reveal").waitFor();
  await shot(page, "reveal-exact", viewport.width, false);
  await page.keyboard.press("Space");
  // Остальные раунды — «Сдаться»; на последней попытке кнопка красная.
  for (;;) {
    const results = page.getByTestId("final-score");
    const giveUp = page.locator("[data-testid=give-up]:enabled");
    const next = page.getByTestId("reveal-next");
    await results
      .or(giveUp.first())
      .or(next)
      .first()
      .waitFor()
      .catch(async (e) => {
        await page.screenshot({ path: path.join(OUT, "debug.png") });
        throw e;
      });
    if (await results.isVisible()) break;
    if (await next.isVisible()) {
      await next.click();
      continue;
    }
    await giveUp.first().click();
    if (
      await page
        .getByTestId("reveal")
        .isVisible()
        .catch(() => false)
    ) {
      await shot(page, "reveal-miss", viewport.width, false);
    }
  }
  await shot(page, "results", viewport.width);
  await context.close();
}

/** Дейлик кино: кадр проясняется по уровням. */
async function film({ browser, viewport }: Ctx): Promise<void> {
  await resetMock();
  const context = await newContext(browser, viewport);
  const page = await context.newPage();
  await login(page, "player");
  await page.goto("/play/film.daily.frame");
  await page.getByTestId("clue-image").waitFor();
  await answer(page, "совсем не то");
  await page.getByTestId("skip").waitFor();
  await shot(page, "game-film", viewport.width);
  await context.close();
}

/** Комната → готовность → матч: лобби ожидания и экран матча. */
async function duel({ browser, viewport }: Ctx): Promise<void> {
  await resetMock();
  const host = await newContext(browser, viewport);
  const rivalCtx = await newContext(browser, { width: 1280, height: 900 });
  const page = await host.newPage();
  const rival = await rivalCtx.newPage();
  await login(page, "player");
  await login(rival, "rival");
  await page.goto("/online");
  const row = page.getByTestId("create-room-music.duel.classic");
  if (!(await row.isVisible())) await page.getByTestId("create-room-toggle").click();
  await row.click();
  await page.getByTestId("room-code").waitFor();
  const code = ((await page.getByTestId("room-code").textContent()) ?? "").trim();
  await rival.goto(`/room/${code}`);
  await rival.getByTestId("join-room").click();
  await page.getByTestId("player-rival").waitFor();
  await shot(page, "room-lobby", viewport.width);
  await page.getByTestId("ready-toggle").click();
  await rival.getByTestId("ready-toggle").click();
  await page.getByTestId("start-match").click();
  await page.getByTestId("clue-audio").waitFor({ state: "attached" });
  await answer(rival, "Кукушка");
  await answer(page, "Тест");
  await page.getByTestId("my-guesses").waitFor();
  await shot(page, "duel", viewport.width);
  await answer(page, "Группа крови");
  await page.getByTestId("reveal-title").waitFor();
  await shot(page, "duel-reveal", viewport.width);
  await host.close();
  await rivalCtx.close();
}

/** Вечеринка на 4: комната-лобби и матч. */
async function party({ browser, viewport }: Ctx): Promise<void> {
  await resetMock();
  const host = await newContext(browser, viewport);
  const page = await host.newPage();
  await login(page, "player");
  const others: BrowserContext[] = [];
  const pages: Page[] = [];
  await page.goto("/online");
  const row = page.getByTestId("create-room-music.lobby.classic");
  if (!(await row.isVisible())) await page.getByTestId("create-room-toggle").click();
  await row.click();
  await page.getByTestId("room-code").waitFor();
  const code = ((await page.getByTestId("room-code").textContent()) ?? "").trim();
  for (const nick of ["rival", "third"]) {
    const c = await newContext(browser, { width: 1280, height: 900 });
    others.push(c);
    const p = await c.newPage();
    await login(p, nick);
    await p.goto(`/room/${code}`);
    await p.getByTestId("join-room").click();
    pages.push(p);
  }
  await page.getByTestId("player-third").waitFor();
  await shot(page, "room-party-lobby", viewport.width);
  for (const p of [page, ...pages]) await p.getByTestId("ready-toggle").click();
  await page.getByTestId("start-match").click();
  await page.getByTestId("clue-audio").waitFor({ state: "attached" });
  await shot(page, "room-party", viewport.width);
  await host.close();
  for (const c of others) await c.close();
}

/** Модалка дневника события: с главной и по прямой ссылке. */
async function event({ browser, viewport }: Ctx): Promise<void> {
  await resetMock({ event: "active" });
  const context = await newContext(browser, viewport);
  const page = await context.newPage();
  await login(page, "player");
  await page.goto("/");
  await page.getByTestId("home-event").click();
  await page.getByTestId("event-modal").waitFor();
  await shot(page, "event-modal", viewport.width, false);
  await page.goto("/events/winter");
  await page.getByTestId("event-modal").waitFor();
  await shot(page, "event-direct", viewport.width, false);
  await context.close();
}

const STATES: Record<string, (c: Ctx) => Promise<void>> = { music, film, duel, party, event };

async function main() {
  const only = process.argv.slice(2);
  const names = only.length ? only : Object.keys(STATES);
  mkdirSync(OUT, { recursive: true });
  const servers = [
    start("npx", ["tsx", "e2e/mocks/server.ts"], { MOCK_PORT: String(MOCK_PORT) }),
    start("npx", ["next", "start", "-p", String(WEB_PORT)], {
      GV_E2E: "1",
      GV_DEV_API_ORIGIN: MOCK,
      GV_BFF_INTERNAL_URL: MOCK,
      NEXT_TELEMETRY_DISABLED: "1",
    }),
  ];
  try {
    await Promise.all([waitFor(`${MOCK}/healthz`), waitFor(`${WEB}/healthz`)]);
    const browser = await chromium.launch();
    for (const name of names) {
      const run = STATES[name];
      if (!run) throw new Error(`нет состояния «${name}»: ${Object.keys(STATES).join(", ")}`);
      for (const viewport of VIEWPORTS) {
        if (process.env.SHOT_WIDTH && Number(process.env.SHOT_WIDTH) !== viewport.width) continue;
        await run({ browser, viewport });
      }
    }
    await browser.close();
  } finally {
    for (const s of servers) stop(s);
  }
}

main().then(
  () => process.exit(0),
  (err) => {
    console.error(err);
    process.exit(1);
  },
);
