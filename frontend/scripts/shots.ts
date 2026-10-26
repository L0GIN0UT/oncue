/**
 * Снимки экранов на моках: каждый маршрут из ROUTES на 1280×900 и 390×844 →
 * `docs/handoff/shots/<route>-<ширина>.png` (маршрут без слэшей: `/` → `home`). Поднимает мок-бэкенд
 * и `next start` (нужна готовая сборка: `pnpm build`), входит как `player`, чтобы личные блоки были
 * заполнены. Запуск: `pnpm shots` (опционально `pnpm shots /solo /shop` — только эти маршруты).
 */
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test";

const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3100);
const MOCK_PORT = Number(process.env.E2E_MOCK_PORT ?? 3101);
const WEB = `http://localhost:${WEB_PORT}`;
const MOCK = `http://127.0.0.1:${MOCK_PORT}`;
const OUT = path.resolve(import.meta.dirname, "../../docs/handoff/shots");

const ROUTES = [
  "/",
  "/solo",
  "/online",
  "/lobbies",
  "/collections",
  "/leaderboards",
  "/profile",
  "/shop",
  "/friends",
  "/notifications",
  "/login",
  "/events/winter",
];
const VIEWPORTS = [
  { width: 1280, height: 900 },
  { width: 390, height: 844 },
] as const;

const slug = (route: string) => (route === "/" ? "home" : route.slice(1).replaceAll("/", "-"));

function start(command: string, args: string[], env: Record<string, string>): ChildProcess {
  return spawn(command, args, {
    env: { ...process.env, ...env },
    stdio: "inherit",
    shell: process.platform === "win32",
  });
}

/** На Windows `kill()` убивает только оболочку: гасим дерево процессов целиком. */
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

async function main() {
  const only = process.argv.slice(2);
  const routes = only.length ? only : ROUTES;
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
    await fetch(`${MOCK}/__mock/reset`, { method: "POST" });
    await fetch(`${MOCK}/__mock/scenario`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ event: "active" }),
    });
    const browser = await chromium.launch();
    for (const viewport of VIEWPORTS) {
      const context = await browser.newContext({
        baseURL: WEB,
        viewport,
        locale: "ru-RU",
        timezoneId: "Europe/Moscow",
        reducedMotion: "reduce",
      });
      const page = await context.newPage();
      await page.goto("/login");
      await page.getByLabel("Почта или ник").fill("player");
      await page.getByLabel("Пароль").fill("correct-horse");
      await page.getByRole("button", { name: "Войти" }).click();
      await page.getByTestId("user-name").waitFor({ state: "attached" });
      for (const route of routes) {
        await page.goto(route);
        await page.waitForLoadState("networkidle");
        const file = path.join(OUT, `${slug(route)}-${viewport.width}.png`);
        await page.screenshot({ path: file, fullPage: true });
        console.log(`снимок: ${path.relative(process.cwd(), file)}`);
      }
      await context.close();
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
