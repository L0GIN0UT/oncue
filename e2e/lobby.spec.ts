/**
 * Полный стек: кастомное лобби «кино по сборнику» этапа 4a (план docs/plans/stage-4.md §0
 * п. 2, 8, 8б; шаблон `film.lobby.collection`).
 *
 * A открывает кураторский сборник засева `seeds/collections/film-lobby.yaml` и жмёт
 * «Играть с друзьями» — закрытое лобби по сборнику с настройками шаблона (12 раундов,
 * 25 с на уровень). B и C входят по коду. Все готовы → смена настроек хостом сбрасывает
 * готовность у всех. Свободный чат лобби: обычное сообщение видно всем, сообщение со словом
 * из denylist (ADR 0030, `gv_text.ugc`) — 422 `match.chat_rejected` и текст отказа, в ленту
 * не попадает. Снова все готовы → отсчёт → раунд 1 (кадр у всех, статусы игроков без текстов
 * ответов) → уровни проходятся пропуском/промахом до итога: места, раунды с «Сохранить».
 *
 * Чтобы итог наступил быстро, хост ставит 5 раундов и 15 с на уровень (варианты
 * `lobby.overridable` шаблона). Пользователи — свежие на прогон (`e2e/online.ts`).
 */
import { expect, test } from "@playwright/test";
import {
  RUN,
  allReady,
  api,
  closePlayers,
  joinByCode,
  json,
  passUntil,
  playerStatus,
  problem,
  readyState,
  registerPlayers,
  roomCode,
  startMatch,
  wrongGuess,
  type Player,
} from "./online";

test.skip(process.env.E2E_SEEDED !== "1", "стек не засеян: нужен шаг засева (E2E_SEEDED=1)");

// Кураторский сборник засева для лобби (≥ 12 фильмов синтетики, seeds/collections/film-lobby.yaml).
const LOBBY_COLLECTION = "Кино · онлайн-лобби: синтетика";
// Шаблон seeds/game/modes/film.lobby.collection.yaml: по умолчанию и выбранные хостом.
const DEFAULT_ROUNDS = "12";
const DEFAULT_WINDOW = "25";
const ROUNDS = 5;
const WINDOW = "15";
// Запись из стартового denylist gv_text (libs/text/src/gv_text/data/ugc_denylist.txt).
const DENIED = "бля";

let players: Player[] = [];
test.afterAll(async () => closePlayers(players));

test("лобби по сборнику: вход по коду, настройки сбрасывают готовность, чат, матч до итога", async ({ browser }) => {
  test.setTimeout(480_000);
  players = await test.step("регистрация A, B, C", () => registerPlayers(browser, ["e2eLa", "e2eLb", "e2eLc"]));
  const [a, b, c] = players as [Player, Player, Player];

  const code = await test.step("A: «Играть с друзьями» на странице сборника → лобби", async () => {
    const list = await json<{ items: { collection_id: string; title: string }[] }>(
      await api(a.page, "collections").get("/collections?curated=true&domain=film&sort=new&limit=50"),
    );
    const collection = list.items.find((x) => x.title === LOBBY_COLLECTION);
    expect(collection, `кураторский сборник «${LOBBY_COLLECTION}» (gv dev seed)`).toBeTruthy();
    await a.page.goto(`/collections/${collection!.collection_id}`);
    await a.page.getByTestId("play-with-friends-start").click();
    const created = await roomCode(a.page);
    await expect(a.page.getByTestId("lobby-collections")).toContainText(LOBBY_COLLECTION);
    await expect(a.page.getByTestId("setting-rounds")).toHaveValue(DEFAULT_ROUNDS);
    await expect(a.page.getByTestId("setting-level_window_s")).toHaveValue(DEFAULT_WINDOW);
    return created;
  });

  await test.step("B и C входят по коду; лобби закрытое — в открытых его нет", async () => {
    await joinByCode(b.page, code);
    await joinByCode(c.page, code);
    await expect(a.page.getByTestId("players").getByRole("listitem")).toHaveCount(3);
    await expect(b.page.getByTestId("lobby-collections")).toContainText(LOBBY_COLLECTION);
    const open = await json<{ items: { code: string }[] }>(await api(b.page, "match").get("/rooms/open"));
    expect(open.items.map((r) => r.code)).not.toContain(code);
  });

  await test.step("хост: 15 с на уровень; все готовы; 5 раундов — готовность сброшена у всех", async () => {
    await a.page.getByTestId("setting-level_window_s").selectOption(WINDOW);
    await expect(c.page.getByTestId("setting-level_window_s")).toHaveValue(WINDOW);
    await allReady(a.page, players);
    await a.page.getByTestId("setting-rounds").selectOption(String(ROUNDS));
    for (const viewer of players) {
      for (const p of players) await expect(readyState(viewer.page, p.nickname)).toHaveText("Не готов");
    }
    await expect(b.page.getByTestId("setting-rounds")).toHaveValue(String(ROUNDS));
    await expect(b.page.getByTestId("setting-rounds")).toBeDisabled();
    await expect(a.page.getByTestId("start-match")).toBeDisabled();
  });

  await test.step("чат лобби: сообщение видно всем, denylist — отказ match.chat_rejected", async () => {
    const hello = `всем привет ${RUN}`;
    await b.page.getByTestId("chat-input").fill(hello);
    await b.page.getByTestId("chat-input").press("Enter");
    await expect(a.page.getByTestId("chat-feed")).toContainText(hello);
    await expect(c.page.getByTestId("chat-feed")).toContainText(hello);

    const denied = `ну ${DENIED} ${RUN}`;
    const [res] = await Promise.all([
      c.page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith(`/rooms/${code}/match/chat`)),
      (async () => {
        await c.page.getByTestId("chat-input").fill(denied);
        await c.page.getByTestId("chat-input").press("Enter");
      })(),
    ]);
    const rejected = await problem(res, 422);
    expect(rejected.code).toBe("match.chat_rejected");
    expect(rejected.details?.reason).toBe("denylist");
    await expect(c.page.getByTestId("chat-error")).toHaveText("Сообщение не отправлено: в нём недопустимые слова.");
    // Текст остаётся в поле для правки; в ленту он не попадает ни у кого.
    await expect(c.page.getByTestId("chat-input")).toHaveValue(denied);
    await expect(a.page.getByTestId("chat-feed").getByText(denied)).toHaveCount(0);
  });

  await test.step("все готовы → отсчёт → раунд 1: кадр у всех", async () => {
    await allReady(a.page, players);
    await startMatch(a.page, players, ROUNDS);
    for (const p of players) await expect(p.page.getByTestId("clue-image")).toBeVisible();
  });

  await test.step("статусы игроков видны всем, текст чужого ответа — никому", async () => {
    for (const p of players) await expect(playerStatus(a.page, p.nickname)).toHaveText("Думает");
    const miss = `мимо-${RUN}-статус`;
    await wrongGuess(b.page, miss);
    // B ответил на уровне (одна попытка), уровень открыт для остальных: у них «Ответил».
    await expect(playerStatus(a.page, b.nickname)).toHaveText("Ответил");
    await expect(playerStatus(c.page, b.nickname)).toHaveText("Ответил");
    await expect(playerStatus(c.page, a.nickname)).toHaveText("Думает");
    await expect(a.page.getByText(miss)).toHaveCount(0);
    await expect(c.page.getByText(miss)).toHaveCount(0);
    await expect(b.page.getByTestId("my-guesses")).toContainText(miss);
  });

  await test.step("уровни и раунды — пропуском/промахом до итога", async () => {
    await passUntil(players, (p) => p.getByTestId("match-summary"));
    for (const p of players) await expect(p.page.getByTestId("match-summary")).toBeVisible({ timeout: 30_000 });
  });

  await test.step("итог: места всех игроков, раунды с «Сохранить»", async () => {
    for (const p of players) await expect(a.page.getByTestId(`standing-${p.nickname}`)).toBeVisible();
    await expect(a.page.locator("[data-testid^=summary-round-]")).toHaveCount(ROUNDS);
    await expect(a.page.getByTestId("summary-round-0").getByTestId("save-button")).toBeVisible();
  });
});
