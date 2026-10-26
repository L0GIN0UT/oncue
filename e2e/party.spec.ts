/**
 * Полный стек: party на 4 `film.party.frame` этапа 4a (план docs/plans/stage-4.md §0 п. 3, 4).
 *
 * A создаёт party из хаба, B, C, D входят по ссылке. Матч: статусы «Думает / Ответил»
 * видны всем, текст чужого ответа — никому (party_speed, parallel). C закрывает вкладку:
 * остальные видят «Отключён» (heartbeat `tick` пропал дольше `GV_MATCH__PRESENCE_TIMEOUT_S`),
 * C возвращается по ссылке до `disconnect_grace_s` (30 с) — снимок `GET /state` с фазой
 * матча и `phase_ends_at`, отметка «Отключён» снимается, C продолжает играть. Конец матча —
 * выход игроков (сдача в party = выбывание с сохранением очков): когда активным остаётся
 * один, матч завершается, у A — итог с местами, у сдавшихся — «Сдался».
 *
 * Пользователи — свежие на прогон (`e2e/online.ts`).
 */
import { expect, test } from "@playwright/test";
import {
  DISCONNECT_MS,
  RUN,
  allReady,
  closePlayers,
  createRoomFromHub,
  forfeit,
  joinByLink,
  json,
  passUntil,
  playerRow,
  playerStatus,
  registerPlayers,
  startMatch,
  wrongGuess,
  type Player,
} from "./online";

test.skip(process.env.E2E_SEEDED !== "1", "стек не засеян: нужен шаг засева (E2E_SEEDED=1)");

const PARTY = "film.party.frame";
// Шаблон seeds/game/modes/film.party.frame.yaml: 10 раундов, 6 уровней, окно 20 с.
const ROUNDS = 10;
const MATCH_PHASES = ["countdown", "level_open", "level_closed", "reveal"];

type RoomState = { room: { phase: string; phase_ends_at: string | null; version: number }; match: unknown };

let players: Player[] = [];
test.afterAll(async () => closePlayers(players));

test("party на 4: статусы, закрытая вкладка и возврат по ссылке, итог", async ({ browser }) => {
  test.setTimeout(420_000);
  players = await test.step("регистрация A, B, C, D", () =>
    registerPlayers(browser, ["e2ePa", "e2ePb", "e2ePc", "e2ePd"]),
  );
  const [a, b, c, d] = players as [Player, Player, Player, Player];

  const code = await test.step("A создаёт party, B, C, D входят по ссылке", async () => {
    const created = await createRoomFromHub(a.page, PARTY);
    for (const p of [b, c, d]) await joinByLink(p.page, created);
    await expect(a.page.getByTestId("players").getByRole("listitem")).toHaveCount(4);
    return created;
  });

  await test.step("все готовы → отсчёт → раунд 1 с кадром у всех", async () => {
    await allReady(a.page, players);
    await startMatch(a.page, players, ROUNDS);
    for (const p of players) await expect(p.page.getByTestId("clue-image")).toBeVisible();
  });

  await test.step("статусы видны всем, текст чужого ответа — никому", async () => {
    for (const p of players) await expect(playerStatus(d.page, p.nickname)).toHaveText("Думает");
    const miss = `мимо-${RUN}-party`;
    await wrongGuess(b.page, miss);
    for (const viewer of [a, c, d]) {
      await expect(playerStatus(viewer.page, b.nickname)).toHaveText("Ответил");
      await expect(viewer.page.getByText(miss)).toHaveCount(0);
    }
    await expect(playerStatus(a.page, d.nickname)).toHaveText("Думает");
  });

  const cContext = c.context;
  await test.step("C закрывает вкладку → у остальных «Отключён»", async () => {
    await c.page.close();
    for (const viewer of [a, b]) {
      await expect(playerRow(viewer.page, c.nickname).getByTestId("presence-disconnected")).toBeVisible({
        timeout: DISCONNECT_MS,
      });
    }
  });

  await test.step("C возвращается по ссылке: снимок с фазой матча и phase_ends_at, игра продолжается", async () => {
    const back = await cContext.newPage();
    const snapshot = back.waitForResponse(
      (r) => r.request().method() === "GET" && r.url().endsWith(`/api/match/v1/rooms/${code}/state`),
    );
    await back.goto(`/room/${code}`);
    const state = await json<RoomState>(await snapshot);
    expect(MATCH_PHASES).toContain(state.room.phase);
    expect(state.room.phase_ends_at).toBeTruthy();
    expect(state.match).toBeTruthy();
    // Участник сразу попадает в матч (без экрана входа в комнату).
    await expect(back.getByTestId("match-stage")).toBeVisible();
    await expect(back.getByTestId("room-preview")).toHaveCount(0);
    // C всё ещё за столом: поле ответа и «Отойти» рисуются только участнику. Если grace
    // (`disconnect_grace_s`) истёк, сервер выбил C (`left`) — падаем здесь с внятной причиной,
    // а не позже на «нет поля ответа».
    await expect(back.getByTestId("away-toggle")).toBeVisible();
    for (const viewer of [a, b]) {
      await expect(playerRow(viewer.page, c.nickname).getByTestId("presence-disconnected")).toHaveCount(0, {
        timeout: DISCONNECT_MS,
      });
    }
    c.page = back;
  });

  await test.step("C продолжает: раунд доигрывается всеми, в следующем раунде ход C виден остальным", async () => {
    // Доиграть раунд 1 до ревила: ходы C с вернувшейся вкладки принимает сервер.
    // Пока C переподключался, таймеры могли прокрутить несколько раундов: номер читаем сейчас.
    const roundLabel = c.page.getByTestId("match-round");
    const roundNow = async (): Promise<number> =>
      Number(/Раунд (\d+)/.exec((await roundLabel.textContent()) ?? "")?.[1] ?? 0);
    const before = await roundNow();
    await passUntil(players, (p) => p.getByTestId("match-reveal"));
    if (before < ROUNDS) {
      await expect.poll(roundNow, { timeout: 30_000 }).toBeGreaterThan(before);
    }
    await wrongGuess(c.page, `мимо-${RUN}-вернулся`);
    // Уровень 1 из 6: после промаха — «Ответил» (или «Мимо», если окно уровня уже ушло дальше).
    await expect(playerStatus(a.page, c.nickname)).toHaveText(/Ответил|Мимо/);
  });

  await test.step("D, C, B выходят из матча (сдача в party) → итог у A", async () => {
    for (const p of [d, c, b]) {
      await expect(p.page.getByTestId("forfeit")).toBeVisible({ timeout: 30_000 });
      await forfeit(p.page);
    }
    await expect(a.page.getByTestId("match-summary")).toBeVisible({ timeout: 30_000 });
    await expect(a.page.getByTestId("summary-headline")).toHaveText("Место 1");
    for (const p of [b, c, d]) {
      await expect(a.page.getByTestId(`standing-${p.nickname}`).getByTestId("standing-finish")).toHaveText("Сдался");
    }
  });
});
