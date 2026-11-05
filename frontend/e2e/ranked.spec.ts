/**
 * Рейтинг и подбор этапа 4b на моках: звания и сезон в хабе и профиле, очередь рейтинговой дуэли
 * (ожидание, «ищем дольше обычного», отмена, соперник найден → комната), дельта рейтинга в итоге
 * и страница лидербордов (пагинация по курсору, «моё место», топ недели).
 */
import type { Page } from "@playwright/test";
import { expect, login, test } from "./fixtures";
import { pickLadder } from "./hubs";
import { QUEUE_TICK_S } from "./mocks/match";

/** Комната матча: `/room/<код из 8 символов>`. */
const ROOM_URL = /\/room\/[A-HJ-NP-Z2-9]{8}$/;

/**
 * Предел ожидания «матч найден → комната»: несколько периодов heartbeat мока и запас на
 * нагруженный CI (сам переход идёт после паузы «Соперник найден», FOUND_DELAY_MS = 900 мс).
 */
const ROOM_WAIT = { timeout: Math.max((QUEUE_TICK_S * 3 + 4) * 1000, 30_000) };

/** Одна попытка ждёт комнату дольше паузы «Соперник найден» (900 мс) и самого перехода. */
const ATTEMPT_MS = 4000;

/**
 * Экран «Соперник найден» живёт ровно `FOUND_DELAY_MS` и сменяется комнатой, поэтому ждать его
 * через `toBeVisible` нельзя: надпись легко пропустить между опросами. Наблюдатель DOM запоминает,
 * что она появлялась (вкладка не перезагружается: переход в комнату клиентский), а тест
 * проверяет это уже после перехода — {@link expectQueueFoundSeen}. Ставить перед входом в очередь.
 */
async function watchQueueFound(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as unknown as { __queueFoundSeen?: boolean };
    w.__queueFoundSeen = false;
    const check = () => {
      if (document.querySelector("[data-testid=queue-found]")) w.__queueFoundSeen = true;
    };
    new MutationObserver(check).observe(document.body, { childList: true, subtree: true });
    check();
  });
}

/** Экран «Соперник найден» показывался (см. {@link watchQueueFound}). */
async function expectQueueFoundSeen(page: Page): Promise<void> {
  const seen = await page.evaluate(
    () => (window as unknown as { __queueFoundSeen?: boolean }).__queueFoundSeen,
  );
  expect(seen, "экран «Соперник найден» не показывался").toBe(true);
}

/**
 * Первому игроку о матче сообщает heartbeat очереди. Ждать его по таймеру нестабильно на
 * нагруженном CI (интервал и нагрузка расходятся), поэтому сами «возвращаем вкладку»:
 * хук очереди на `visibilitychange` делает `tick` сразу (так же, как при реальном возврате
 * на вкладку). Ждём результат — URL комнаты, а не промежуточную надпись: она живёт 900 мс.
 * Повторные tick безвредны: после «матч найден» переход уже запланирован.
 */
async function expectQueueFound(page: Page, room: RegExp | string = ROOM_URL): Promise<void> {
  await expect(async () => {
    await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    await expect(page).toHaveURL(room, { timeout: ATTEMPT_MS });
  }).toPass(ROOM_WAIT);
  await expectQueueFoundSeen(page);
}

test("хаб: звание со ступенью, калибровка вместо звания, сезон и топ недели; то же в профиле", async ({
  page,
  expectAccessible,
}) => {
  await login(page);
  await page.goto("/online");
  await pickLadder(page, "music");
  const music = page.getByTestId("rating-music");
  await expect(music.getByTestId("tier")).toHaveText("Бронза 2");
  await expect(music).toContainText("1120");
  await expect(music.getByTestId("next-tier")).toContainText("До следующего звания: 80 очков");
  await expect(music.getByTestId("season-music")).toHaveText("Сезон 3 · осталось 12 дней");
  // В калибровке звание скрыто.
  await pickLadder(page, "film");
  const film = page.getByTestId("rating-film");
  await expect(film.getByTestId("calibration")).toHaveText("Калибровка: осталось 7 матчей");
  await expect(film.getByTestId("tier")).toHaveCount(0);
  await expect(page.getByTestId("weekly-film")).toContainText("игрок1");
  await expectAccessible(page);

  await page.goto("/me");
  await expect(page.getByTestId("rating-music").getByTestId("tier")).toHaveText("Бронза 2");
});

test("очередь: ожидание, подсказка, отмена; второй игрок находит матч, оба в комнате; дельта рейтинга в итоге", async ({
  page,
  player,
  scenario,
  expectAccessible,
}) => {
  await login(page);
  await page.goto("/online");
  await pickLadder(page, "music");
  await expect(page.getByTestId("queue-enter-music-classic")).toHaveText("Найти соперника");
  await pickLadder(page, "film");
  await page.getByTestId("queue-format-film-lite").click();
  await expect(page.getByTestId("queue-enter-film-lite")).toHaveText("Найти соперника");
  await pickLadder(page, "music");

  // Ожидание с подсказкой и отменой.
  await scenario({ match: { queueSlow: true } });
  await pickLadder(page, "music");
  await page.getByTestId("queue-enter-music-classic").click();
  await expect(page.getByTestId("queue-searching")).toContainText(
    "Ищем соперника: Музыка · Классика",
  );
  await expect(page.getByTestId("queue-slow")).toContainText("дольше обычного");
  await expectAccessible(page);
  await page.getByTestId("queue-cancel").click();
  await expect(page.getByTestId("queue-searching")).toHaveCount(0);
  await expect(page.getByTestId("queue-enter-music-classic")).toBeEnabled();

  // Снова в очередь; соперник встаёт в ту же — его перебрасывает в комнату сразу.
  await pickLadder(page, "music");
  await watchQueueFound(page);
  await page.getByTestId("queue-enter-music-classic").click();
  await expect(page.getByTestId("queue-searching")).toBeVisible();
  const rival = await player("rival");
  await rival.goto("/online");
  await pickLadder(rival, "music");
  await watchQueueFound(rival);
  await rival.getByTestId("queue-enter-music-classic").click();
  await expect(rival).toHaveURL(ROOM_URL);
  await expectQueueFoundSeen(rival);
  const code = (await rival.getByTestId("room-code").textContent())!.trim();

  // Первому о матче сообщает heartbeat; вкладка видима — переход в комнату.
  await expectQueueFound(page, `/room/${code}`);
  await expect(page.getByTestId("room-code")).toHaveText(code);
  await expect(page.getByTestId("player-rival")).toBeVisible();

  // Матч: соперник сдаётся → у победителя дельта рейтинга, у сдавшегося — калибровка и штраф.
  await page.getByTestId("ready-toggle").click();
  await rival.getByTestId("ready-toggle").click();
  await page.getByTestId("start-match").click();
  await expect(rival.getByTestId("clue-audio")).toBeAttached();
  await rival.getByTestId("forfeit").click();
  await rival.getByTestId("forfeit-confirm").click();
  await expect(page.getByTestId("summary-headline")).toHaveText("Соперник сдался");

  const change = page.getByTestId("rating-change");
  await expect(change.getByTestId("rating-delta")).toHaveText("+16", { timeout: 10_000 });
  await expect(change).toContainText("1195 → 1211");
  await expect(change.getByTestId("rating-tier")).toHaveText("Новое звание: Серебро 1");
  await expect(page.getByTestId("standings")).toBeVisible();

  const loserChange = rival.getByTestId("rating-change");
  await expect(loserChange.getByTestId("calibration")).toHaveText("Калибровка: осталось 9 матчей", {
    timeout: 10_000,
  });
  await expect(loserChange.getByTestId("rating-penalty")).toContainText("−10");
  await expectAccessible(page);
});

test("стык очередь → комната: запись снята, tick отвечает 404, а первый игрок всё равно попадает в комнату", async ({
  page,
  player,
  scenario,
}) => {
  await scenario({ match: { queueRecordGone: true } });
  await login(page);
  await page.goto("/online");
  await pickLadder(page, "music");
  await watchQueueFound(page);
  await page.getByTestId("queue-enter-music-classic").click();
  await expect(page.getByTestId("queue-searching")).toBeVisible();

  const rival = await player("rival");
  await rival.goto("/online");
  await pickLadder(rival, "music");
  await watchQueueFound(rival);
  await rival.getByTestId("queue-enter-music-classic").click();
  await expect(rival).toHaveURL(ROOM_URL);
  await expectQueueFoundSeen(rival);
  const code = (await rival.getByTestId("room-code").textContent())!.trim();

  // Первому игроку tick отвечает 404, но «Вас убрали из очереди» не появляется: клиент спрашивает
  // активную комнату и входит в неё.
  await expectQueueFound(page, `/room/${code}`);
  await expect(page.getByTestId("queue-error")).toHaveCount(0);
  await expect(page.getByTestId("player-rival")).toBeVisible();
});

test("rating недоступен: итог рейтингового матча цел, блок — «появится позже»", async ({
  page,
  player,
  scenario,
}) => {
  await scenario({ match: { ratingDown: true } });
  await login(page);
  await page.goto("/online");
  await pickLadder(page, "film");
  await watchQueueFound(page);
  await page.getByTestId("queue-enter-film-classic").click();
  const rival = await player("rival");
  await rival.goto("/online");
  await pickLadder(rival, "film");
  await rival.getByTestId("queue-enter-film-classic").click();
  await expectQueueFound(page);
  await page.getByTestId("ready-toggle").click();
  await rival.getByTestId("ready-toggle").click();
  await page.getByTestId("start-match").click();
  await expect(rival.getByTestId("forfeit")).toBeVisible();
  await rival.getByTestId("forfeit").click();
  await rival.getByTestId("forfeit-confirm").click();
  await expect(page.getByTestId("summary-headline")).toHaveText("Соперник сдался");
  await expect(page.getByTestId("rating-change-later")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("standings")).toBeVisible();
});

test("лидерборды: ladder-ы, пагинация по курсору, топ недели; «Моё место» только вошедшему", async ({
  page,
  expectAccessible,
}) => {
  // Страница публична: гость видит таблицу, но не «Моё место».
  await page.goto("/leaderboards");
  const rows = page.locator("[data-testid^=board-row-]");
  await expect(rows).toHaveCount(10);
  await expect(page.getByTestId("board-row-1")).toContainText("Платина 3");
  await expect(page.getByTestId("around-me")).toHaveCount(0);
  await page.getByRole("tab", { name: "Топ недели" }).click();
  await expect(page.getByTestId("weekly-film")).toContainText("игрок1");
  await expectAccessible(page);
  await page.getByRole("tab", { name: "Рейтинг" }).click();

  await page.getByTestId("board-more").click();
  await expect(rows).toHaveCount(20);
  await page.getByTestId("board-more").click();
  await expect(rows).toHaveCount(25);
  await expect(page.getByTestId("board-more")).toHaveCount(0);

  await page.getByRole("radio", { name: "Музыка" }).click();
  await expect(page.getByRole("radio", { name: "Музыка" })).toHaveAttribute("aria-checked", "true");
  await expect(rows).toHaveCount(10);
  await page.getByRole("tab", { name: "Топ недели" }).click();
  await expect(page.getByTestId("weekly-music")).toBeVisible();
  await page.getByRole("tab", { name: "Рейтинг" }).click();

  await login(page);
  await page.goto("/leaderboards");
  await page.getByTestId("around-me").click();
  await expect(page.getByTestId("board-row-18")).toHaveAttribute("aria-current", "true");
  await expect(page.getByTestId("board-row-18")).toContainText("(вы)");
  await page.getByTestId("around-me").click();
  await expect(page.getByTestId("board-row-1")).toBeVisible();
});
