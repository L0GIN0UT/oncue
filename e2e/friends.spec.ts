/**
 * Полный стек: друзья, блокировки, приватность и приглашение в комнату 5c (ADR 0050-0053).
 *
 * Один последовательный сценарий на засеянном стеке (`gv dev seed`, конфиг social опубликован
 * bootstrap-задачей). Игроки A, B, C регистрируются через UI. Сценарии:
 * 1. A шлёт заявку B по нику -> у B уведомление `friend_request` -> B принимает -> у A
 *    `friend_accepted`, оба видят друг друга в списке.
 * 2. Код добавления: C вводит код A (заявка), отзывает её; A перевыпускает код -> старый код
 *    отклонён единым «игрок не найден».
 * 3. B блокирует A: дружба разорвана, заявка A по нику отвечает так же, как для неизвестного
 *    ника; разблокировка в «Блокировках», после неё дружба восстанавливается новой заявкой.
 * 4. Приватность: B скрывает профиль -> посторонний C на `/players/{B}` видит только `restricted`.
 * 5. Приглашение: A создаёт комнату и зовёт друга B (онлайн по presence) -> у B `room_invite`
 *    с кнопкой «Войти» -> B в комнате.
 *
 * Доставка асинхронная (outbox -> Redpanda -> notifications; колокольчик — realtime или опрос
 * раз в ~60 с; статус «онлайн» — presence раз в 30 с): ожидания по состояниям, таймаут <= 90 с.
 */
import { expect, test, type Page } from "@playwright/test";
import { userIdOf } from "./admin";
import {
  api,
  apiErrorsOf,
  closePlayers,
  createRoomFromHub,
  json,
  registerPlayers,
  RUN,
  type Player,
} from "./online";

test.skip(process.env.E2E_SEEDED !== "1", "стек не засеян: нужен шаг засева (E2E_SEEDED=1)");
test.describe.configure({ mode: "serial", timeout: 240_000 });

const DUEL = "music.duel.classic";
const DELIVERY_TIMEOUT_MS = 90_000;

type Notice = { id: string; type: string; data?: { room_code?: string } | null };

let a: Player;
let b: Player;
let c: Player;
let idA = "";
let idB = "";

test.beforeAll(async ({ browser }) => {
  test.setTimeout(240_000);
  [a, b, c] = (await registerPlayers(browser, ["fra", "frb", "frc"])) as [Player, Player, Player];
  idA = await userIdOf(a.page);
  idB = await userIdOf(b.page);
});

test.afterAll(async () => {
  await closePlayers([a, b, c].filter(Boolean));
});

test.afterEach(async ({}, testInfo) => {
  if (a && testInfo.status !== testInfo.expectedStatus)
    await testInfo.attach("api-errors", { body: apiErrorsOf([a.page, b.page, c.page]) });
});

// ----------------------------------------------------------------------------- помощники
const noticesOf = async (page: Page): Promise<Notice[]> =>
  (await json<{ items: Notice[] }>(await api(page, "notifications").get("/notifications?limit=50")))
    .items;

async function waitNotice(page: Page, type: string): Promise<Notice> {
  let found: Notice | undefined;
  await expect
    .poll(async () => (found = (await noticesOf(page)).find((n) => n.type === type)) !== undefined, {
      timeout: DELIVERY_TIMEOUT_MS,
      message: `уведомление ${type}`,
    })
    .toBe(true);
  return found!;
}

const tab = (page: Page, name: "friends" | "requests" | "add" | "blocks" | "privacy") =>
  page.goto(`/friends?tab=${name}`);

/** Заявка в друзья по нику через вкладку «Добавить». */
async function sendByNickname(from: Page, nickname: string): Promise<void> {
  await tab(from, "add");
  await from.getByTestId("add-nickname").fill(nickname);
  await from.getByTestId("add-nickname-submit").click();
}

/** Принять входящую заявку от `nickname` во вкладке «Заявки». */
async function accept(page: Page, nickname: string): Promise<void> {
  await tab(page, "requests");
  const row = page.getByTestId(`request-in-${nickname}`);
  await expect(row).toBeVisible();
  const [res] = await Promise.all([
    page.waitForResponse(
      (r) => r.request().method() === "POST" && /\/friend-requests\/[^/]+\/accept$/.test(r.url()),
    ),
    row.getByTestId("request-accept").click(),
  ]);
  expect(res.status(), await res.text()).toBe(200);
}

async function expectFriends(page: Page, nickname: string): Promise<void> {
  await tab(page, "friends");
  await expect(page.getByTestId(`friend-${nickname}`)).toBeVisible();
}

// ----------------------------------------------------------------------------- сценарии
test("заявка по нику: уведомление, принятие, друзья видят друг друга", async () => {
  await sendByNickname(a.page, b.nickname);
  await expect(a.page.getByTestId("add-sent")).toContainText(b.nickname);

  await waitNotice(b.page, "friend_request");
  await b.page.goto("/notifications");
  await expect(b.page.locator("[data-testid=notification-item][data-type=friend_request]")).toHaveCount(1);
  // Колокольчик: счётчик непрочитанного (realtime или опрос).
  await expect(b.page.getByTestId("notification-count")).toBeVisible({ timeout: DELIVERY_TIMEOUT_MS });

  await accept(b.page, a.nickname);
  await waitNotice(a.page, "friend_accepted");
  await expectFriends(a.page, b.nickname);
  await expectFriends(b.page, a.nickname);
});

test("код добавления: заявка по коду, перевыпуск отклоняет старый код", async () => {
  await tab(a.page, "add");
  const myCode = a.page.getByTestId("my-code");
  await expect(myCode).toBeVisible();
  const oldCode = (await myCode.textContent())!.trim();
  expect(oldCode).toMatch(/^[A-Z0-9]{4,16}$/);

  // C использует код A: заявка уходит.
  await tab(c.page, "add");
  await c.page.getByTestId("add-code").fill(oldCode.toLowerCase()); // фронтенд приводит к верхнему регистру
  await c.page.getByTestId("add-code-submit").click();
  await expect(c.page.getByTestId("add-sent")).toContainText(a.nickname);

  // C отзывает заявку, чтобы проверка старого кода не упёрлась в «заявка уже отправлена».
  await tab(c.page, "requests");
  await c.page.getByTestId(`request-out-${a.nickname}`).getByTestId("request-cancel").click();
  await expect(c.page.getByTestId(`request-out-${a.nickname}`)).toHaveCount(0);

  // A перевыпускает код.
  const [regen] = await Promise.all([
    a.page.waitForResponse(
      (r) => r.request().method() === "POST" && r.url().endsWith("/friend-code/regenerate"),
    ),
    a.page.getByTestId("code-regenerate").click(),
  ]);
  expect(regen.status(), await regen.text()).toBe(200);
  await expect(myCode).not.toHaveText(oldCode);

  // Старый код: отказ, как у неизвестного кода (единый ответ).
  await tab(c.page, "add");
  await c.page.getByTestId("add-code").fill(oldCode);
  await c.page.getByTestId("add-code-submit").click();
  await expect(c.page.getByTestId("social-error")).toBeVisible();
  await expect(c.page.getByTestId("add-sent")).toHaveCount(0);
});

test("блокировка рвёт дружбу; заявка заблокированному неотличима от «игрок не найден»", async () => {
  // Эталон: текст ошибки для заведомо несуществующего ника.
  await sendByNickname(c.page, `nobody${RUN}`);
  const unknown = await c.page.getByTestId("social-error").textContent();
  expect(unknown).toBeTruthy();

  // B блокирует A в профиле A.
  await b.page.goto(`/players/${idA}`);
  await b.page.getByTestId("player-is-friend").waitFor();
  await b.page.getByTestId("player-block").click();
  await b.page.getByTestId("player-block-confirm").click();
  await expect(b.page.getByTestId("player-blocked")).toBeVisible();

  // У A дружба разорвана, заявка B по нику — тот же отказ, что у неизвестного ника.
  await tab(a.page, "friends");
  await expect(a.page.getByTestId("friends-empty")).toBeVisible();
  await sendByNickname(a.page, b.nickname);
  await expect(a.page.getByTestId("social-error")).toHaveText(unknown!);
  await expect(a.page.getByTestId("add-sent")).toHaveCount(0);

  // B снимает блокировку во вкладке «Блокировки».
  await tab(b.page, "blocks");
  await expect(b.page.getByTestId(`block-${a.nickname}`)).toBeVisible();
  await b.page.getByTestId(`block-${a.nickname}`).getByTestId("unblock").click();
  await expect(b.page.getByTestId("blocks-empty")).toBeVisible();

  // Дружба не возвращается сама; после разблокировки заявка A проходит, B принимает.
  await sendByNickname(a.page, b.nickname);
  await expect(a.page.getByTestId("add-sent")).toContainText(b.nickname);
  await accept(b.page, a.nickname);
  await expectFriends(a.page, b.nickname);
});

test("приватность: скрытый профиль видит посторонний только в виде restricted", async () => {
  await tab(b.page, "privacy");
  const profile = b.page.getByTestId("privacy-profile");
  await expect(profile).toBeChecked();
  const [saved] = await Promise.all([
    b.page.waitForResponse((r) => r.request().method() === "PUT" && r.url().endsWith("/me/privacy")),
    profile.uncheck(),
  ]);
  expect(saved.status(), await saved.text()).toBe(200);

  try {
    await c.page.goto(`/players/${idB}`);
    await expect(c.page.getByTestId("player-restricted")).toBeVisible();
    await expect(c.page.getByTestId("player-actions")).toHaveCount(0);
  } finally {
    await tab(b.page, "privacy");
    if (!(await b.page.getByTestId("privacy-profile").isChecked()))
      await b.page.getByTestId("privacy-profile").check();
  }
  await expect(b.page.getByTestId("privacy-profile")).toBeChecked();
  await c.page.goto(`/players/${idB}`);
  await expect(c.page.getByTestId("player-restricted")).toHaveCount(0);
});

test("приглашение в комнату: уведомление room_invite, «Войти», игрок в комнате", async () => {
  // B остаётся на странице с шапкой: presence-сигнал раз в 30 с делает его «онлайн».
  await b.page.goto("/notifications");

  const code = await createRoomFromHub(a.page, DUEL);
  // Список «онлайн-друзей» лобби освежается раз в 30 с; B виден после первого сигнала.
  const row = a.page.getByTestId(`invite-${b.nickname}`);
  await expect(row).toBeVisible({ timeout: DELIVERY_TIMEOUT_MS });
  await row.getByTestId("invite-send").click();
  await expect(row.getByTestId("invite-sent")).toBeVisible();

  const invite = await waitNotice(b.page, "room_invite");
  expect(invite.data?.room_code).toBe(code);

  await b.page.goto("/notifications");
  const item = b.page.locator("[data-testid=notification-item][data-type=room_invite]");
  await expect(item).toHaveCount(1);
  await item.getByTestId("notification-join").click();
  await expect(b.page).toHaveURL(new RegExp(`/room/${code}$`));
  await expect(b.page.getByTestId("room-preview")).toBeVisible();
  await b.page.getByTestId("join-room").click();
  await expect(b.page.getByTestId("room-code")).toHaveText(code);
  await expect(a.page.getByTestId(`player-${b.nickname}`)).toBeVisible();
});
