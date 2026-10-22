/**
 * Полный стек: дуэль 1×1 `music.duel.classic` этапа 4a (план docs/plans/stage-4.md §0 п. 5, 8).
 *
 * A создаёт дуэль из хаба «Онлайн», B входит по ссылке, оба готовы → отсчёт → первый раунд.
 * Ответы тесту неизвестны (план раундов — только в снимке комнаты match), поэтому
 * «угадывание» проверяется промахом: попытки уровня убывают, свой ответ виден только себе,
 * сопернику — ни текста, ни попытки. Уровень 1 оба пропускают, на уровне 2 (пропуска нет)
 * исчерпывают 3 попытки — раунд закрывается ревилом у обоих. Во втором раунде B сдаётся:
 * у A «Соперник сдался», у B «Поражение», в итоге — раунд 1 с «Сохранить». Реванш по
 * согласию обоих — новая комната с теми же игроками; итог есть в «Моих матчах».
 *
 * Пользователи — свежие на прогон (`e2e/online.ts`). Селекторы — `data-testid` фронтенда
 * (`frontend/src/features/match`).
 */
import { expect, test } from "@playwright/test";
import {
  RUN,
  allReady,
  answerInput,
  closePlayers,
  createRoomFromHub,
  forfeit,
  joinByLink,
  playerRow,
  registerPlayers,
  roomCode,
  skipLevel,
  startMatch,
  wrongGuess,
  type Player,
} from "./online";

test.skip(process.env.E2E_SEEDED !== "1", "стек не засеян: нужен шаг засева (E2E_SEEDED=1)");

const DUEL = "music.duel.classic";
// Конфиг шаблона seeds/game/modes/music.duel.classic.yaml: 7 раундов, 2 уровня, 3 попытки.
const ROUNDS = 7;
const ATTEMPTS = 3;

let players: Player[] = [];
test.afterAll(async () => closePlayers(players));

test("дуэль: промах, пропуск, ревил у обоих, «Соперник сдался», реванш", async ({ browser }) => {
  test.setTimeout(300_000);
  players = await test.step("регистрация A и B", () => registerPlayers(browser, ["e2eDa", "e2eDb"]));
  const [a, b] = players as [Player, Player];

  const code = await test.step("A создаёт дуэль, B входит по ссылке", async () => {
    const created = await createRoomFromHub(a.page, DUEL);
    await joinByLink(b.page, created);
    await expect(playerRow(a.page, b.nickname)).toBeVisible();
    // В дуэли нет настроек хоста и свободного чата — только эмоции и фразы (ADR 0036).
    await expect(a.page.locator("[data-testid^=setting-]")).toHaveCount(0);
    await expect(a.page.getByTestId("chat-input")).toHaveCount(0);
    await expect(a.page.getByTestId("start-match")).toBeDisabled();
    return created;
  });

  await test.step("готовность обоих → отсчёт → раунд 1 с аудио у обоих", async () => {
    await allReady(a.page, players);
    await startMatch(a.page, players, ROUNDS);
    for (const p of players) {
      await expect(p.page.getByTestId("match-level")).toHaveText("Уровень 1 из 2");
      await expect(p.page.getByTestId("clue-audio")).toBeAttached();
      await expect(p.page.getByTestId("phase-timer")).toBeVisible();
    }
  });

  const miss = `мимо-${RUN}`;
  await test.step("промах A: попытки убывают, текст ответа сопернику не виден", async () => {
    await wrongGuess(a.page, miss);
    await expect(a.page.getByTestId("attempts-left")).toHaveText(`Осталось ${ATTEMPTS - 1} попытки`);
    await expect(a.page.getByTestId("my-guesses")).toContainText(miss);
    await expect(b.page.getByTestId("my-guesses")).toHaveCount(0);
    await expect(b.page.getByText(miss)).toHaveCount(0);
    // Соперник по-прежнему может ходить: промах не закрывает уровень в race.
    await expect(answerInput(b.page)).toBeEnabled();
  });

  await test.step("уровень 1 пропускают оба → уровень 2 у обоих", async () => {
    await skipLevel(a.page);
    await skipLevel(b.page);
    for (const p of players) await expect(p.page.getByTestId("match-level")).toHaveText("Уровень 2 из 2");
  });

  await test.step("уровень 2: три промаха каждого → ревил раунда у обоих", async () => {
    for (let n = 0; n < ATTEMPTS; n++) await wrongGuess(a.page, `${miss}-a-${n}`);
    // A исчерпал попытки, но уровень открыт, пока может ходить B (race без точного ответа).
    await expect(a.page.getByTestId("attempts-left")).toHaveText("Попыток не осталось");
    await expect(answerInput(a.page)).toBeDisabled();
    await expect(answerInput(b.page)).toBeEnabled();
    for (let n = 0; n < ATTEMPTS; n++) await wrongGuess(b.page, `${miss}-b-${n}`);
    for (const p of players) await expect(p.page.getByTestId("reveal-title")).not.toBeEmpty();
    for (const p of players) await expect(p.page.getByTestId("match-round")).toHaveText(`Раунд 2 из ${ROUNDS}`, { timeout: 30_000 });
  });

  await test.step("B сдаётся: «Соперник сдался» у A, «Поражение» у B", async () => {
    await forfeit(b.page);
    await expect(a.page.getByTestId("summary-headline")).toHaveText("Соперник сдался");
    await expect(b.page.getByTestId("summary-headline")).toHaveText("Поражение");
    await expect(a.page.getByTestId(`standing-${b.nickname}`).getByTestId("standing-finish")).toHaveText("Сдался");
    // Итог из Postgres (bff /pages/match): сыгранный раунд с «Сохранить» (library).
    const round = a.page.getByTestId("summary-round-0");
    await expect(round).toBeVisible();
    await expect(round.getByTestId("save-button")).toBeVisible();
  });

  await test.step("реванш: согласие обоих → новая комната с теми же игроками", async () => {
    await a.page.getByTestId("rematch").click();
    await expect(a.page.getByTestId("rematch")).toHaveText("Ждём согласия остальных…");
    await b.page.getByTestId("rematch").click();
    const rematch = await roomCode(b.page);
    expect(rematch).not.toBe(code);
    await expect(a.page).toHaveURL(`/room/${rematch}`);
    await expect(a.page.getByTestId("room-code")).toHaveText(rematch);
    await expect(playerRow(a.page, b.nickname)).toBeVisible();
  });

  await test.step("«Мои матчи» у A: победа, переход к итогу", async () => {
    await a.page.goto("/me");
    const card = a.page.locator("[data-testid^=my-match-]").first();
    await expect(card).toContainText("Победа");
    await card.click();
    await expect(a.page).toHaveURL(/\/match\//);
    await expect(a.page.getByTestId("summary-headline")).toHaveText("Соперник сдался");
  });
});
