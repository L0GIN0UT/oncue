/**
 * Полный стек: режимы этапа 3a (план docs/plans/stage-3a.md §10) на засеянном стеке.
 *
 * Сценарии: каталог режимов, марафон с выбором режиссёра, «больше/меньше» (метрика
 * загаданной скрыта до ответа), недельная сетка до чемпиона, загадка `description`
 * (текстовый JSON-ассет), ревил через 2 минуты (часы Playwright + `final-clue`) и запрос
 * ассетов через admin-API game (`asset-requests`, ADR 0025).
 *
 * Ответы тесту неизвестны: раунды завершаются «Сдаться» или первым вариантом выбора. Пул —
 * синтетика `seeds/catalog/synthetic-*.jsonl` («Синтетический режиссёр», 40 фильмов), засев —
 * `gv dev seed`. Селекторы — `data-testid` (контракт с фронтендом перечислен у каждого шага).
 */
import { randomUUID } from "node:crypto";
import { expect, request as playwrightRequest, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

// Сценариям нужен засеянный стек (каталог, исходники, ассеты, наборы периода) — как daily.spec.ts.
test.skip(process.env.E2E_SEEDED !== "1", "стек не засеян: нужен шаг засева (E2E_SEEDED=1)");

const MARATHON = "film.marathon.director";
const PAIR = "film.pair.rating";
const BRACKET = "film.bracket.weekly";
const DESCRIPTION = "film.random.description";
const BLACKOUT = "film.random.blackout";
const SOUNDTRACK = "film.random.soundtrack";
const DIRECTOR = "Синтетический режиссёр";

type Json = Record<string, unknown>;
type Round = {
  index: number;
  status: string;
  reveal: { metric: number | null; final_clue: { url: string; expires_at: string } } | null;
  shown: { title: string; metric: number | null }[];
  options: { option_id: string; title: string | null }[];
  progress: { lives_left: number | null; streak: number | null; stage: string | null } | null;
};
type Snapshot = { id: string; version: number; status: string; current_round: number | null; rounds: Round[] };

// ----------------------------------------------------------------------------- игрок
/** Публичный API game от имени гостя страницы: cookie доступа + CSRF + Origin. */
async function guestApi(page: Page) {
  const csrf = async () => (await page.context().cookies()).find((c) => c.name === "gv_csrf")?.value ?? "";
  // Запросы собираются руками — Origin ставим сами (cookie-мутации требуют его вместе с CSRF).
  const origin = new URL(page.url()).origin;
  return {
    get: (path: string) => page.request.get(`/api/game/v1${path}`),
    post: async (path: string, data?: Json) =>
      page.request.post(`/api/game/v1${path}`, {
        data,
        headers: { "X-CSRF-Token": await csrf(), Origin: origin, "Idempotency-Key": randomUUID() },
      }),
  };
}

async function snapshot(page: Page, sessionId: string): Promise<Snapshot> {
  const res = await (await guestApi(page)).get(`/sessions/${sessionId}`);
  expect(res.status()).toBe(200);
  return (await res.json()) as Snapshot;
}

function sessionIdOf(page: Page): string {
  return page.url().split("/").pop()!;
}

/** Ход клиента: POST ответа/сдачи/пропуска (ждём ответ, чтобы не кликнуть дважды по старой версии). */
function isMove(url: string, method: string): boolean {
  return method === "POST" && /\/api\/game\/v1\/sessions\/[^/]+\/(guesses|skips|rounds\/\d+\/give-up)$/.test(url);
}

/**
 * Довести сессию до итога: «Дальше»/«К итогам» (`reveal-next`), иначе — действие раунда
 * (`give-up` или первый `pick-option-*`). Каждый ход ждёт ответ сервера.
 */
async function playUntilResults(page: Page, action: Locator, maxSteps = 120): Promise<void> {
  const next = page.getByTestId("reveal-next");
  const done = page.getByTestId("final-score");
  for (let step = 0; step < maxSteps; step++) {
    await expect(done.or(next).or(action).first()).toBeVisible();
    if (await done.isVisible()) return;
    if (await next.isVisible()) {
      await next.click();
      continue;
    }
    await Promise.all([
      page.waitForResponse((r) => isMove(r.url(), r.request().method())),
      action.first().click(),
    ]);
  }
  throw new Error(`сессия не дошла до итога за ${maxSteps} шагов`);
}

const giveUp = (page: Page) => page.locator("[data-testid=give-up]:enabled");
const pickOption = (page: Page) => page.locator("[data-testid^=pick-option-]:enabled");

// ----------------------------------------------------------------------------- сценарии
test("каталог: карточки новых режимов и недельная сетка среди «дня»", async ({ page }) => {
  // Витрина: домен — переключатель, тип режима — вкладка, строка режима раскрывается по клику.
  const tabs: Record<string, string> = { random: "Быстрые", marathon: "Марафоны", pair: "Пары" };
  for (const key of [MARATHON, "music.marathon.artist", PAIR, "music.pair.popularity", DESCRIPTION, BLACKOUT, SOUNDTRACK]) {
    const [domain, source] = key.split(".");
    await page.goto("/solo");
    await page.getByRole("radio", { name: domain === "film" ? "Кино" : "Музыка" }).click();
    await page.getByRole("tab", { name: tabs[source!]! }).click();
    await expect(page.getByTestId(`mode-card-${key}`), key).toBeVisible();
  }
});

test("марафон: выбор режиссёра, три жизни, итог", async ({ page }) => {
  await page.goto("/");
  // Гостевую сессию главная заводит асинхронно (восстановление личных блоков): ждём cookie CSRF —
  // значит, ручной запрос /subjects пройдёт аутентификацию.
  await expect
    .poll(async () => (await page.context().cookies()).some((c) => c.name === "gv_csrf"), {
      timeout: 15_000,
    })
    .toBe(true);
  const api = await guestApi(page);
  // Персоны марафона — из проекции game: синтетический режиссёр с ≥ 5 играбельными фильмами.
  const found = await api.get(`/modes/${MARATHON}/subjects?q=${encodeURIComponent("Синтетический")}`);
  expect(found.status()).toBe(200);
  const subjects = (await found.json()) as { subject_id: string; name: string; role: string; items: number }[];
  const director = subjects.find((s) => s.name === DIRECTOR);
  expect(director, "синтетический режиссёр в /subjects").toBeDefined();
  expect(director!.role).toBe("director");
  expect(director!.items).toBeGreaterThanOrEqual(32);

  await page.goto(`/play/${MARATHON}`);
  const picker = page.getByTestId("subject-picker");
  await expect(picker).toBeVisible();
  await picker.getByTestId("subject-search").fill("Синтетический");
  await page.getByTestId(`subject-option-${director!.subject_id}`).click();
  await page.getByTestId("subject-start").click();
  await expect(page).toHaveURL(/\/play\/s\//);
  const sessionId = sessionIdOf(page);

  await expect(page.getByTestId("lives")).toContainText("3");
  const first = await snapshot(page, sessionId);
  expect(first.rounds[0]?.progress?.lives_left).toBe(3);
  // Раунды выдаются лениво: до ответа в снимке нет ответа текущего раунда.
  expect(JSON.stringify(first.rounds[0])).not.toMatch(/media_item_id|"title"/);

  await playUntilResults(page, giveUp(page));
  await expect(page).toHaveURL(new RegExp(`/results/${sessionId}`));
  // Три сдачи — три потерянные жизни: ровно три сыгранных раунда.
  await expect(page.getByTestId("result-row")).toHaveCount(3);
});

test("больше/меньше: метрика загаданной скрыта до ответа, видна после", async ({ page }) => {
  await page.goto(`/play/${PAIR}`);
  await expect(page).toHaveURL(/\/play\/s\//);
  const sessionId = sessionIdOf(page);

  await expect(page.getByTestId("shown-metric")).toHaveText(/\d/);
  await expect(page.getByTestId("streak")).toContainText("0");
  const before = await snapshot(page, sessionId);
  const round = before.rounds[0]!;
  expect(round.reveal).toBeNull();
  expect(round.shown).toHaveLength(1);
  expect(round.shown[0]!.metric).not.toBeNull();
  expect(round.options.map((o) => o.option_id).sort()).toEqual(["higher", "lower"]);
  expect(round.options.every((o) => o.title === null)).toBe(true);
  // Античит: в снимке одна метрика — опорной единицы; загаданной (и её id) нет.
  expect(JSON.stringify(round).match(/"metric":\s*-?\d/g) ?? []).toHaveLength(1);
  expect(JSON.stringify(before)).not.toMatch(/media_item_id/);

  const [move] = await Promise.all([
    page.waitForResponse((r) => isMove(r.url(), r.request().method())),
    page.getByTestId("pick-option-higher").click(),
  ]);
  const result = (await move.json()) as { round: Round };
  expect(result.round.reveal?.metric).toEqual(expect.any(Number));
  await expect(page.getByTestId("reveal-metric")).toHaveText(/\d/);

  // Одна жизнь: серия идёт до первой ошибки (цепочка синтетики ≤ 40 пар).
  await playUntilResults(page, pickOption(page));
  await expect(page).toHaveURL(new RegExp(`/results/${sessionId}`));
  await expect(page.getByTestId("final-score")).toHaveText(/\d/);
});

test("недельная сетка: 31 выбор до чемпиона", async ({ page }) => {
  test.setTimeout(240_000);
  const stats: (number | null)[] = [];
  page.on("response", async (r) => {
    if (r.request().method() === "POST" && /\/guesses$/.test(r.url()) && r.ok()) {
      stats.push(((await r.json()) as { stats: { agree_pct: number } | null }).stats?.agree_pct ?? null);
    }
  });

  await page.goto(`/play/${BRACKET}`);
  await expect(page).toHaveURL(/\/play\/s\//);
  const sessionId = sessionIdOf(page);

  // Первый тур — 1/16 (16 пар), варианты — две единицы пары с названиями.
  await expect(page.getByTestId("bracket-stage")).toBeVisible();
  await expect(pickOption(page)).toHaveCount(2);
  const first = await snapshot(page, sessionId);
  expect(first.rounds).toHaveLength(31);
  expect(first.rounds[0]!.options.every((o) => o.title)).toBe(true);

  await Promise.all([
    page.waitForResponse((r) => isMove(r.url(), r.request().method())),
    pickOption(page).first().click(),
  ]);
  // Статистика выбора: если сервер её отдал — фронтенд показывает процент согласных.
  if (stats[0] !== null && stats[0] !== undefined) await expect(page.getByTestId("agree-pct")).toBeVisible();

  await playUntilResults(page, pickOption(page), 80);
  await expect(page).toHaveURL(new RegExp(`/results/${sessionId}`));
  await expect(page.getByTestId("bracket-champion")).toBeVisible();
  const result = (await (await guestApi(page)).get(`/sessions/${sessionId}/result`)).json() as Promise<Json>;
  const bracket = (await result).bracket as { champion: unknown; picks: unknown[] } | null;
  expect(bracket?.champion).toBeTruthy();
  expect(bracket?.picks).toHaveLength(31);

  // Одна попытка на период: повторный вход ведёт на итог.
  await page.goto(`/play/${BRACKET}`);
  await expect(page).toHaveURL(new RegExp(`/results/${sessionId}`));
});

test("описание: уровень — текстовый JSON-ассет, текст на экране", async ({ page }) => {
  const clueUrls: string[] = [];
  page.on("response", async (r) => {
    if (/\/rounds\/\d+\/clue$/.test(r.url()) && r.ok()) clueUrls.push(((await r.json()) as { url: string }).url);
  });

  await page.goto(`/play/${DESCRIPTION}`);
  await expect(page).toHaveURL(/\/play\/s\//);
  const textClue = page.getByTestId("text-clue");
  // Уровень 1 — слоган синтетического фильма (все тексты засева одобрены, ADR 0024).
  await expect(textClue).toContainText(/Синтетическая история №\d+/);

  expect(clueUrls[0]).toMatch(/^\/media\/a\/[0-9a-f]+\.json\?/);
  const asset = await page.request.get(clueUrls[0]!);
  expect(asset.status()).toBe(200);
  expect(asset.headers()["content-type"]).toContain("application/json");
  const body = (await asset.json()) as { blocks: { kind: string; text?: string }[] };
  expect(body.blocks[0]?.kind).toBe("tagline");

  await playUntilResults(page, giveUp(page));
  await expect(page.getByTestId("result-row")).toHaveCount(5);
});

test("ревил через 2 минуты: свежая ссылка final-clue", async ({ page }) => {
  // Часы браузера под контролем теста: ссылка ревила (TTL 60 с) «истекает» без ожидания.
  await page.clock.install();
  await page.goto(`/play/${BLACKOUT}`);
  await expect(page).toHaveURL(/\/play\/s\//);
  const sessionId = sessionIdOf(page);
  const api = await guestApi(page);

  // До конца раунда ревила нет.
  const early = await api.post(`/sessions/${sessionId}/rounds/0/final-clue`);
  expect(early.status()).toBe(409);
  expect(((await early.json()) as { code: string }).code).toBe("game.round_not_finished");

  await Promise.all([
    page.waitForResponse((r) => isMove(r.url(), r.request().method())),
    giveUp(page).click(),
  ]);
  // Ревил audio_blackout — кадр (ADR 0026).
  await expect(page.getByTestId("reveal-image")).toBeVisible();

  // Через 2 минуты исходная ссылка истекла: фронтенд берёт свежую через final-clue.
  const refreshed = page.waitForRequest((r) => r.method() === "POST" && /\/rounds\/0\/final-clue$/.test(r.url()));
  await page.clock.fastForward("02:00");
  await refreshed;
  await expect(page.getByTestId("reveal-image")).toBeVisible();

  const fresh = await api.post(`/sessions/${sessionId}/rounds/0/final-clue`);
  expect(fresh.status()).toBe(200);
  const clue = (await fresh.json()) as { url: string; mime: string; kind: string };
  expect(clue.mime).toMatch(/^image\//);
  const image = await page.request.get(clue.url);
  expect(image.status()).toBe(200);
  expect(image.headers()["content-type"]).toMatch(/^image\//);
});

// ----------------------------------------------------------------------------- админ
const ADMIN = {
  login: process.env.GV_BOOTSTRAP_ADMIN__LOGIN,
  password: process.env.GV_BOOTSTRAP_ADMIN__PASSWORD,
  secret: process.env.GV_SERVICE_SECRET_ADMIN,
  identity: process.env.GV_SEED__IDENTITY_URL ?? "http://127.0.0.1:8100",
  game: process.env.GV_SEED__GAME_URL ?? "http://127.0.0.1:8103",
  media: process.env.GV_SEED__MEDIA_URL ?? "http://127.0.0.1:8102",
};

/** admin-токен как у `gv dev seed`: сервисный токен клиента `admin` → вход в админку. */
async function adminToken(): Promise<string> {
  const identity = await playwrightRequest.newContext({ baseURL: ADMIN.identity });
  try {
    const svc = await identity.post("/internal/v1/service-tokens", {
      data: { client_id: "admin", client_secret: ADMIN.secret, audience: "identity" },
    });
    expect(svc.status()).toBe(200);
    const session = await identity.post("/internal/v1/admin-sessions", {
      headers: { Authorization: `Bearer ${((await svc.json()) as { access_token: string }).access_token}` },
      data: { login: ADMIN.login, password: ADMIN.password },
    });
    expect(session.status()).toBe(200);
    return ((await session.json()) as { access_token: string }).access_token;
  } finally {
    await identity.dispose();
  }
}

async function adminContext(baseURL: string, token: string): Promise<APIRequestContext> {
  return playwrightRequest.newContext({ baseURL, extraHTTPHeaders: { Authorization: `Bearer ${token}` } });
}

const mutation = () => ({ "Idempotency-Key": randomUUID() });
const REASON = "e2e: запрос ассетов";

test.describe("admin-API", () => {
  test.skip(!ADMIN.login || !ADMIN.password || !ADMIN.secret, "нет учётных данных админа (GV_BOOTSTRAP_ADMIN__*, GV_SERVICE_SECRET_ADMIN)");

  test("запрос ассетов под новые параметры загадки черновика", async () => {
    const token = await adminToken();
    const game = await adminContext(ADMIN.game, token);
    const media = await adminContext(ADMIN.media, token);
    try {
      const detail = (await (await game.get(`/admin/v1/mode-configs/${SOUNDTRACK}`)).json()) as {
        published_version: number;
        draft: { revision: number } | null;
      };
      const published = (await (
        await game.get(`/admin/v1/mode-configs/${SOUNDTRACK}/versions/${detail.published_version}`)
      ).json()) as { body: { clue: { params: Json } } };
      let revision: number | null = detail.draft?.revision ?? null;

      const saveDraft = async (body: Json) => {
        const res = await game.put(`/admin/v1/mode-configs/${SOUNDTRACK}/draft`, {
          headers: mutation(),
          data: { body, base_revision: revision, reason: REASON },
        });
        expect(res.status(), await res.text()).toBe(200);
        const draft = (await res.json()) as { revision: number; validation: { warnings: { code: string; path: string | null }[] } };
        revision = draft.revision;
        return draft;
      };
      const requestAssets = () =>
        game.post(`/admin/v1/mode-configs/${SOUNDTRACK}/asset-requests`, {
          headers: mutation(),
          data: { draft_revision: revision, reason: REASON },
        });

      try {
        // Параметры опубликованной версии: ассеты уже есть — 409.
        await saveDraft(published.body);
        const same = await requestAssets();
        expect(same.status()).toBe(409);
        expect(((await same.json()) as { code: string }).code).toBe("game.assets_already_available");

        // Новый отрывок: start_s 5 (синтетический саундтрек — 50 с).
        const changed = structuredClone(published.body);
        changed.clue.params = { ...changed.clue.params, start_s: 5 };
        const draft = await saveDraft(changed);
        const asked = await requestAssets();
        // Повторный прогон: профиль уже досоздан прошлым запуском — тоже успех.
        if (asked.status() === 409) {
          expect(((await asked.json()) as { code: string }).code).toBe("game.assets_already_available");
          return;
        }
        expect(draft.validation.warnings).toContainEqual(expect.objectContaining({ code: "assets.profile_missing", path: "clue.params" }));
        expect(asked.status(), await asked.text()).toBe(201);
        const created = (await asked.json()) as { request_id: string; status: string; params_hash: string; clue_type: string };
        expect(["pending", "sent"]).toContain(created.status);
        expect(created.clue_type).toBe("soundtrack");

        const listed = (await (await game.get(`/admin/v1/mode-configs/${SOUNDTRACK}/asset-requests`)).json()) as { request_id: string }[];
        expect(listed.map((r) => r.request_id)).toContain(created.request_id);

        // ClueAssetsRequested доходит до media-pipeline: появляется динамический профиль.
        await expect
          .poll(
            async () => {
              const res = await media.get(`/admin/v1/profiles?clue_type=soundtrack&params_hash=${created.params_hash}`);
              const rows = (await res.json()) as { origin: string; status: string }[];
              return rows.find((p) => p.origin === "dynamic")?.status ?? "missing";
            },
            { timeout: 60_000 },
          )
          .toMatch(/^(active|backfilling)$/);
      } finally {
        // Черновик возвращаем к опубликованным параметрам: другие сценарии его не видят, но
        // повторный прогон начинается с чистого состояния.
        await saveDraft(published.body);
      }
    } finally {
      await game.dispose();
      await media.dispose();
    }
  });
});
