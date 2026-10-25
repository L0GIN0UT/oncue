/**
 * Онлайн этапа 4a на моках: несколько контекстов браузера в одной комнате (лобби →
 * готовность → матч → итог), реконнект и ресинк по версии, фолбэк на tick, экран регистрации
 * для гостя, отказ чата. Канал — подменённый транспорт (e2e/realtime.ts).
 */
import type { Page } from "@playwright/test";
import { expect, login, test } from "./fixtures";

/** Ответить свободным текстом (мок сверяет название без регистра). */
async function answer(page: Page, text: string) {
  const input = page.getByRole("combobox");
  await expect(input).toBeEnabled();
  await input.fill(text);
  await input.press("Enter");
}

/** Хост создаёт комнату из хаба; возвращает код. */
async function createRoom(page: Page, modeKey: string): Promise<string> {
  await page.goto("/online");
  await page.getByTestId(`create-room-${modeKey}`).click();
  await expect(page).toHaveURL(/\/room\/[A-HJ-NP-Z2-9]{8}$/);
  return (await page.getByTestId("room-code").textContent())!.trim();
}

async function joinRoom(page: Page, code: string) {
  await page.goto(`/room/${code}`);
  await expect(page.getByTestId("room-preview")).toBeVisible();
  await page.getByTestId("join-room").click();
  await expect(page.getByTestId("room-code")).toHaveText(code);
}

const status = (page: Page, nickname: string) =>
  page.getByTestId(`player-${nickname}`).getByTestId("player-status");

test("гость и аноним видят «Онлайн доступен после регистрации», вход возвращает в онлайн", async ({
  page,
  expectAccessible,
}) => {
  for (const path of ["/online", "/lobbies", "/room/K7MQ2XWP", "/me"]) {
    await page.goto(path);
    await expect(page.getByTestId("online-auth-required")).toBeVisible();
  }
  await expectAccessible(page);

  // Гостевая сессия (соло без аккаунта) — тот же экран, API match отвечает 403.
  await page.request.post("/api/identity/v1/auth/guest");
  await page.goto("/online");
  await expect(page.getByTestId("online-auth-required")).toBeVisible();
  await expect(page.getByTestId("online-login")).toHaveAttribute("href", "/login?next=%2Fonline");
  const res = await page.request.get("/api/match/v1/rooms/open");
  expect(res.status()).toBe(403);
  expect((await res.json()).code).toBe("auth.guest_forbidden");

  await page.request.post("/api/identity/v1/auth/logout");
  await page.goto("/room/K7MQ2XWP");
  await page.getByTestId("online-login").click();
  await expect(page).toHaveURL(/\/login\?next=%2Froom%2FK7MQ2XWP$/);
  await page.getByLabel("E-mail или ник").fill("player");
  await page.getByLabel("Пароль").fill("correct-horse");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).toHaveURL(/\/room\/K7MQ2XWP$/);
  await expect(page.getByTestId("room-missing")).toBeVisible();
});

test("дуэль: лобби → готовность → матч → «Соперник сдался» → итог и «Мои матчи»", async ({
  page,
  player,
  scenario,
  expectAccessible,
}) => {
  await login(page);
  await expect(page.getByTestId("nav-online")).toBeVisible();
  const code = await createRoom(page, "music.duel.classic");
  const rival = await player("rival");
  await joinRoom(rival, code);
  await expect(page.getByTestId("player-rival")).toBeVisible();
  await expectAccessible(page);

  // Старт недоступен, пока не все готовы.
  await expect(page.getByTestId("start-match")).toBeDisabled();
  await page.getByTestId("ready-toggle").click();
  await rival.getByTestId("ready-toggle").click();
  await expect(page.getByTestId("player-rival").getByTestId("ready-state")).toHaveText("Готов");

  // Срыв старта (план раундов не получен): причина видна всем, кнопка старта снова доступна.
  await scenario({ match: { startFailOnce: "match.game_unavailable" } });
  await page.getByTestId("start-match").click();
  for (const p of [page, rival])
    await expect(p.getByTestId("start-error")).toContainText("Онлайн временно недоступен");
  await expect(page.getByTestId("start-match")).toBeEnabled();
  await page.getByTestId("start-match").click();

  // Отсчёт и первый уровень у обоих.
  await expect(rival.getByTestId("countdown")).toBeVisible();
  await expect(page.getByTestId("clue-audio")).toBeAttached();
  await expect(rival.getByTestId("clue-audio")).toBeAttached();
  await expect(page.getByTestId("match-round")).toHaveText("Раунд 1 из 2");
  await expect(page.getByTestId("phase-timer")).toBeVisible();

  // Промах соперника: своя попытка видна только ему, у хоста — статус без текста.
  await answer(rival, "Кукушка");
  await expect(rival.getByTestId("attempts-left")).toHaveText("Осталось 2 попытки");
  await expect(rival.getByTestId("my-guesses")).toContainText("Кукушка");
  await expect(page.getByText("Кукушка")).toHaveCount(0);

  // Хост угадывает — уровень закрывается для обоих (race), ревил у обоих.
  await answer(page, "Группа крови");
  await expect(page.getByTestId("reveal-title")).toHaveText("Группа крови");
  await expect(rival.getByTestId("reveal-title")).toHaveText("Группа крови");
  await expect(page.getByTestId("player-player").getByTestId("player-score")).toContainText("12");

  // Эмоция видна сопернику пузырём.
  await rival.getByTestId("emote-match.emotes.gg").click();
  await expect(page.getByTestId("player-rival").getByTestId("emote-bubble")).toHaveText("🤝 GG");

  // «Отошёл / Вернулся»: отметка видна сопернику.
  await rival.getByTestId("away-toggle").click();
  await expect(page.getByTestId("player-rival")).toContainText("Отошёл");
  await rival.getByTestId("away-toggle").click();
  await expect(page.getByTestId("player-rival")).not.toContainText("Отошёл");

  // Второй раунд: соперник сдаётся.
  await expect(rival.getByTestId("match-round")).toHaveText("Раунд 2 из 2");
  await rival.getByTestId("forfeit").click();
  await rival.getByTestId("forfeit-confirm").click();
  await expect(page.getByTestId("summary-headline")).toHaveText("Соперник сдался");
  await expect(rival.getByTestId("summary-headline")).toHaveText("Поражение");
  await expect(page.getByTestId("standing-rival").getByTestId("standing-finish")).toHaveText(
    "Сдался",
  );
  await expect(page.getByTestId("summary-round-0")).toContainText("Группа крови");
  await expect(page.getByTestId("summary-round-0").getByTestId("save-button")).toBeVisible();

  // Кнопки действий над соперником: друзья (решение social через bff), жалоба на игрока и аватарку.
  await expect(page.getByTestId("rate-rival").getByTestId("rate-add-friend")).toBeVisible();
  await expect(page.getByTestId("rate-rival").getByTestId("rate-report")).toBeVisible();
  await expect(page.getByTestId("rate-rival").getByTestId("rate-report-avatar")).toBeVisible();

  // Оценка соперника: похвала уходит один раз.
  await page.getByTestId("rate-rival").getByTestId("rate-praise").click();
  await expect(page.getByTestId("rate-rival")).toContainText("похвала отправлена");
  await expectAccessible(page);

  // Реванш: когда согласны все — новая комната с теми же игроками.
  await page.getByTestId("rematch").click();
  await expect(page.getByTestId("rematch")).toHaveText("Ждём согласия остальных…");
  await rival.getByTestId("rematch").click();
  // Оба проголосовавших переходят в новую комнату: соперник — по ответу команды, хост — по
  // дельте `closed {reason: rematch, rematch_code}`.
  await expect(rival).toHaveURL(/\/room\/[A-HJ-NP-Z2-9]{8}$/);
  await expect(rival.getByTestId("room-code")).not.toHaveText(code);
  const rematchCode = (await rival.getByTestId("room-code").textContent())!.trim();
  await expect(page).toHaveURL(`/room/${rematchCode}`);
  await expect(page.getByTestId("room-code")).toHaveText(rematchCode);
  await expect(page.getByTestId("player-rival")).toBeVisible();

  // «Мои матчи» в профиле → итог матча.
  await page.goto("/me");
  const card = page.locator("[data-testid^=my-match-]").first();
  await expect(card).toContainText("Победа");
  await card.click();
  await expect(page).toHaveURL(/\/match\//);
  await expect(page.getByTestId("summary-headline")).toHaveText("Соперник сдался");

  // История в хабе: жалоба на игрока матча (bff отдаёт participants, ADR 0069).
  await page.goto("/online");
  await expect(page.getByTestId("history-report-rival").first()).toBeVisible();
});

test("лобби на 4: вход по коду, настройки хоста сбрасывают готовность, статусы, чат и отказ чата", async ({
  page,
  player,
  scenario,
}) => {
  await login(page);
  const code = await createRoom(page, "music.lobby.classic");
  const others = await Promise.all(["rival", "third", "fourth"].map((n) => player(n)));
  for (const p of others) {
    // Код вводят строчными — поле приводит к верхнему регистру.
    await p.goto("/lobbies");
    await p.getByTestId("room-code-input").fill(code.toLowerCase());
    await expect(p.getByTestId("room-code-input")).toHaveValue(code);
    await p.getByTestId("room-code-submit").click();
    await p.getByTestId("join-room").click();
    await expect(p.getByTestId("room-code")).toHaveText(code);
  }
  await expect(page.getByTestId("players").getByRole("listitem")).toHaveCount(4);

  // Все готовы; хост меняет число раундов — готовность сброшена у всех.
  for (const p of [page, ...others]) await p.getByTestId("ready-toggle").click();
  await expect(page.getByTestId("player-fourth").getByTestId("ready-state")).toHaveText("Готов");
  await page.getByTestId("setting-rounds").selectOption("3");
  for (const p of [page, ...others])
    await expect(p.getByTestId("player-rival").getByTestId("ready-state")).toHaveText("Не готов");
  await expect(others[0]!.getByTestId("setting-rounds")).toHaveValue("3");
  await expect(others[0]!.getByTestId("setting-rounds")).toBeDisabled();

  // Свободный чат (кастомное лобби): текст — как текст; слово из denylist — отказ.
  await others[0]!.getByTestId("chat-input").fill("<b>всем привет</b>");
  await others[0]!.getByTestId("chat-input").press("Enter");
  await expect(page.getByTestId("chat-feed")).toContainText("<b>всем привет</b>");
  await scenario({ match: { chatDeny: ["запретка"] } });
  await others[1]!.getByTestId("chat-input").fill("тут запретка");
  await others[1]!.getByTestId("chat-input").press("Enter");
  await expect(others[1]!.getByTestId("chat-error")).toHaveText(
    "Сообщение не отправлено: в нём недопустимые слова.",
  );
  await expect(others[1]!.getByTestId("chat-input")).toHaveValue("тут запретка");
  await expect(page.getByTestId("chat-feed")).not.toContainText("запретка");

  for (const p of [page, ...others]) await p.getByTestId("ready-toggle").click();
  await page.getByTestId("start-match").click();
  await expect(page.getByTestId("match-round")).toHaveText("Раунд 1 из 3");

  // Статусы «Думает / Мимо / Угадал» видны всем, текст ответа — никому.
  await expect(status(page, "third")).toHaveText("Думает");
  await answer(others[1]!, "Неверный ответ");
  await expect(status(page, "third")).toHaveText("Мимо");
  await answer(others[0]!, "Группа крови");
  await expect(status(others[2]!, "rival")).toHaveText("Угадал");
  await expect(page.getByText("Неверный ответ")).toHaveCount(0);
  await answer(page, "Группа крови");
  await answer(others[2]!, "Тоже мимо");
  await expect(page.getByTestId("reveal-title")).toHaveText("Группа крови");
});

test("ресинк по версии и реконнект: дыра в канале, «Отключён», фолбэк на tick, «нет связи»", async ({
  page,
  player,
  scenario,
}) => {
  test.setTimeout(90_000);
  // Порог «Отключён» больше периода tick (5 с при живом канале).
  await scenario({ match: { disconnectAfterMs: 7_000, revealMs: 4_000 } });
  await login(page);
  const code = await createRoom(page, "film.party.frame");
  const rival = await player("rival");
  await joinRoom(rival, code);
  await expect(page.getByTestId("player-rival")).toBeVisible();

  // Пропущенная версия в канале: следующее сообщение приходит с дырой → GET /state.
  const resync = page.waitForRequest((r) => r.url().endsWith(`/rooms/${code}/state`));
  await scenario({ match: { dropNextMessage: true } });
  await rival.getByTestId("ready-toggle").click();
  await expect(rival.getByTestId("ready-toggle")).toHaveAttribute("aria-pressed", "true");
  await rival.getByTestId("emote-match.emotes.wow").click();
  await resync;
  await expect(page.getByTestId("player-rival").getByTestId("ready-state")).toHaveText("Готов");
  await page.getByTestId("ready-toggle").click();

  // Канал упал: матч идёт на HTTP-tick (раз в секунду).
  await scenario({ match: { feedDown: true } });
  await page.getByTestId("start-match").click();
  await expect(page.getByTestId("clue-image")).toBeVisible({ timeout: 10_000 });
  await expect(rival.getByTestId("clue-image")).toBeVisible({ timeout: 10_000 });
  await answer(rival, "Брат");
  await answer(page, "Не тот фильм");
  await expect(page.getByTestId("reveal-title")).toHaveText("Брат", { timeout: 10_000 });
  await scenario({ match: { feedDown: false } });

  // Соперник закрыл вкладку — «Отключён»; вернулся по ссылке — снова в матче.
  const rivalContext = rival.context();
  await rival.close();
  await expect(page.getByTestId("player-rival").getByTestId("presence-disconnected")).toBeVisible({
    timeout: 15_000,
  });
  const back = await rivalContext.newPage();
  await back.goto(`/room/${code}`);
  await expect(back.getByTestId("match-stage")).toBeVisible();
  await expect(page.getByTestId("player-rival").getByTestId("presence-disconnected")).toHaveCount(
    0,
    {
      timeout: 10_000,
    },
  );

  // Ни канала, ни tick 20 с — баннер «нет связи»; связь вернулась — баннер снят.
  await back.route(/\/api\/match\//, (route) => route.abort());
  await expect(back.getByTestId("offline-banner")).toBeVisible({ timeout: 30_000 });
  await back.unroute(/\/api\/match\//);
  await expect(back.getByTestId("offline-banner")).toHaveCount(0, { timeout: 10_000 });
});

test("хаб: открытые лобби и вход в комнату из списка; неверный код — подсказка", async ({
  page,
  player,
  expectAccessible,
}) => {
  await login(page);
  const code = await createRoom(page, "music.lobby.classic");
  await page.getByTestId("setting-visibility").selectOption("public");
  const rival = await player("rival");
  await rival.goto("/online");
  await expect(rival.getByTestId(`open-room-${code}`)).toBeVisible();
  await expectAccessible(rival);
  await rival.getByTestId("room-code-input").fill("ABC");
  await rival.getByTestId("room-code-submit").click();
  await expect(rival.getByTestId("room-code-error")).toContainText("8 символов");
  await rival.getByTestId(`open-room-${code}`).getByRole("link").click();
  await rival.getByTestId("join-room").click();
  await expect(page.getByTestId("player-rival")).toBeVisible();
});

test("регистрация из экрана онлайна возвращает в онлайн после подтверждения кодом", async ({
  page,
}) => {
  await page.goto("/online");
  await page.getByTestId("online-register").click();
  await expect(page).toHaveURL(/\/register\?next=%2Fonline$/);
  await page.getByLabel("Ник").fill("newcomer");
  await page.getByLabel("E-mail").fill("newcomer@example.com");
  await page.getByLabel("Пароль").fill("long-enough-pass");
  for (const box of await page.getByRole("checkbox").all()) await box.check();
  await page.getByRole("button", { name: "Зарегистрироваться" }).click();
  await page.getByTestId("code-input").fill("123456");
  await page.getByTestId("code-submit").click();
  await expect(page).toHaveURL(/\/online$/);
  await expect(page.getByTestId("join-by-code")).toBeVisible();
});

test("сборник: «Играть с друзьями» создаёт лобби по сборнику; у музыки без шаблона кнопки нет", async ({
  page,
  player,
}) => {
  await login(page);
  await page.goto("/collections/00000000-0000-4000-a000-000000000003");
  await expect(page.getByTestId("collection-play")).toBeVisible();
  await expect(page.getByTestId("play-with-friends")).toHaveCount(0);

  await page.goto("/collections/00000000-0000-4000-a000-000000000001");
  await page.getByTestId("play-with-friends-start").click();
  await expect(page).toHaveURL(/\/room\/[A-HJ-NP-Z2-9]{8}$/);
  await expect(page.getByTestId("lobby-collections")).toContainText("Кино · кадры: классика");
  const code = (await page.getByTestId("room-code").textContent())!.trim();

  const rival = await player("rival");
  await joinRoom(rival, code);
  await expect(rival.getByTestId("lobby-collections")).toContainText("Кино · кадры: классика");
  for (const p of [page, rival]) await p.getByTestId("ready-toggle").click();
  await page.getByTestId("start-match").click();
  await expect(page.getByTestId("clue-image")).toBeVisible();
});

test("вход с 202 и запаздывающим снимком; поле ответа — по своему состоянию уровня, новый уровень без своих команд", async ({
  page,
  player,
  scenario,
}) => {
  await login(page);
  const code = await createRoom(page, "music.duel.classic");
  const rival = await player("rival");

  // Вход ответил 202, а снимок ещё 1,5 с не знает игрока: клиент повторяет GET /state.
  await scenario({ match: { acceptOnce: true, joinStateLagMs: 1_500 } });
  await rival.goto(`/room/${code}`);
  await rival.getByTestId("join-room").click();
  await expect(rival.getByTestId("room-code")).toHaveText(code, { timeout: 10_000 });
  await expect(rival.getByTestId("room-fatal")).toHaveCount(0);

  for (const p of [page, rival]) await p.getByTestId("ready-toggle").click();
  await expect(page.getByTestId("player-rival").getByTestId("ready-state")).toHaveText("Готов");
  await page.getByTestId("start-match").click();
  await expect(page.getByTestId("match-level")).toHaveText("Уровень 1 из 2");

  // Свой пропуск: поле и «Пропустить» заблокированы, повторного хода нет.
  await expect(page.getByRole("combobox")).toBeEnabled();
  await page.getByTestId("match-skip").click();
  await expect(page.getByRole("combobox")).toBeDisabled();
  await expect(page.getByTestId("match-skip")).toBeDisabled();

  // Соперник тоже пропускает — сервер открывает уровень 2: поле снова активно без своих команд,
  // сразу по статусу «думает» из канала, не дожидаясь снимка (GET /state здесь медленный).
  await page.route(/\/rooms\/[A-Z0-9]+\/state$/, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 4_000));
    await route.continue();
  });
  await rival.getByTestId("match-skip").click();
  await expect(page.getByTestId("match-level")).toHaveText("Уровень 2 из 2");
  await expect(page.getByRole("combobox")).toBeEnabled({ timeout: 2_000 });
  await page.unroute(/\/rooms\/[A-Z0-9]+\/state$/);
  await expect(rival.getByRole("combobox")).toBeEnabled();

  // Промахи до конца попыток блокируют поле только у промахнувшегося.
  for (const guess of ["Мимо 1", "Мимо 2", "Мимо 3"]) await answer(rival, guess);
  await expect(rival.getByTestId("attempts-left")).toHaveText("Попыток не осталось");
  await expect(rival.getByRole("combobox")).toBeDisabled();
  await expect(page.getByRole("combobox")).toBeEnabled();
});
