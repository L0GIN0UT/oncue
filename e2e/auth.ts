/**
 * Общие шаги e2e: регистрация и вход через UI с кодами из писем (ADR 0041).
 *
 * В local/ci identity пишет письма в лог (`LogEmailSender`, `backend=log`): строка
 * `email.logged` несёт `challenge_id` и `code` (адреса получателя в ней нет). Код берётся из
 * `docker compose logs identity` по `challenge_id`, который клиент получает в ответе API, —
 * поэтому параллельные регистрации друг другу не мешают.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { expect, test, type Page, type Response } from "@playwright/test";

const COMPOSE_DIR = process.env.E2E_COMPOSE_DIR;
const COMPOSE_FILES = (
  process.env.E2E_COMPOSE_FILES ??
  "-f compose.infra.yml -f compose.infra.dev.yml -f compose.services.yml -f compose.services.dev.yml -f compose.web.yml -f compose.gateway.yml"
)
  .split(/\s+/)
  .filter(Boolean);

// eslint-disable-next-line no-control-regex
const ANSI = /\u001b\[[0-9;]*m/g;
/** `code=123456` (консольный лог) или `"code": "123456"` (JSON); `purpose=code_login` не подходит. */
const CODE = /\bcode\W{1,4}(\d{4,10})\b/;

/** Код из письма по `challenge_id` или `null`, пока письма нет в логе identity. */
export function codeFor(challengeId: string): string | null {
  const cwd =
    COMPOSE_DIR ?? path.resolve(test.info().project.testDir, "../infra/compose");
  const logs = execFileSync(
    "docker",
    [
      "compose",
      "--env-file",
      ".env",
      ...COMPOSE_FILES,
      "logs",
      "--no-color",
      "--since",
      "15m",
      "identity",
    ],
    { cwd, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 },
  );
  const lines = logs
    .split("\n")
    .map((line) => line.replace(ANSI, ""))
    .filter((line) => line.includes("email.logged") && line.includes(challengeId));
  // Повторная отправка меняет код в той же записи — нужно самое свежее письмо.
  return lines.map((line) => CODE.exec(line)?.[1] ?? null).filter(Boolean).at(-1) ?? null;
}

/**
 * Отправить форму с учётом лимита gateway `auth_per_ip` (30 в минуту с одного IP): e2e
 * регистрирует десятки игроков с одного адреса, 429 — подождать и повторить.
 */
export async function submitThrottled(
  page: Page,
  button: string,
  apiPath: string,
): Promise<Response> {
  for (let attempt = 0; attempt < 20; attempt++) {
    const response = page.waitForResponse(
      (r) => new URL(r.url()).pathname === apiPath && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: button }).click();
    const done = await response;
    if (done.status() !== 429) return done;
    await page.waitForTimeout(3_000); // окно лимита nginx — 2 с на запрос; пауза здесь осознанна
  }
  throw new Error(`${apiPath}: лимит gateway не отпустил за 20 попыток`);
}

/** Ввести в открытом шаге кода письмо с `challengeId` (ждём, пока оно появится в логе). */
export async function enterCode(page: Page, challengeId: string): Promise<void> {
  let code: string | null = null;
  await expect
    .poll(() => (code = codeFor(challengeId)), {
      timeout: 30_000,
      message: `код для ${challengeId} в логе identity`,
    })
    .not.toBeNull();
  await page.getByTestId("code-input").fill(code!);
  await page.getByTestId("code-submit").click();
}

/** Регистрация через UI → код из письма → сессия (подтверждение входит в аккаунт). */
export async function registerViaUi(
  page: Page,
  nickname: string,
  password: string,
): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Ник").fill(nickname);
  await page.getByLabel("E-mail").fill(`${nickname}@example.com`);
  await page.getByLabel("Пароль").fill(password);
  for (const box of await page.getByRole("checkbox").all()) await box.check();
  const response = await submitThrottled(
    page,
    "Зарегистрироваться",
    "/api/identity/v1/auth/register",
  );
  expect(response.status(), await response.text()).toBe(202);
  const challenge = (await response.json()) as { challenge_id: string };
  await enterCode(page, challenge.challenge_id);
  await expect(page.getByTestId("user-name")).toHaveText(nickname, { timeout: 30_000 });
}

/**
 * Вход через UI. Новый браузерный контекст — неизвестное устройство: identity отвечает
 * `code_required`, и шаг кода проходится так же, как при регистрации.
 */
export async function loginViaUi(
  page: Page,
  nickname: string,
  password: string,
): Promise<{ codeRequired: boolean }> {
  await page.goto("/login");
  await page.getByLabel("E-mail или ник").fill(nickname);
  await page.getByLabel("Пароль").fill(password);
  const response = await submitThrottled(page, "Войти", "/api/identity/v1/auth/login");
  expect(response.status(), await response.text()).toBe(200);
  const body = (await response.json()) as {
    status: "authenticated" | "code_required";
    challenge?: { challenge_id: string };
  };
  const codeRequired = body.status === "code_required";
  if (codeRequired) await enterCode(page, body.challenge!.challenge_id);
  await expect(page.getByTestId("user-name")).toHaveText(nickname);
  return { codeRequired };
}
