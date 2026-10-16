/**
 * Полный стек: сборники и медиатека этапа 3b (план docs/plans/stage-3b.md §0 п. 2–7).
 *
 * Один последовательный сценарий на засеянном стеке (`gv dev seed`: каталог с синтетикой 3a,
 * исходники, кураторские сборники `seeds/collections`):
 * 1. Пользователи A и B регистрируются через UI. Письмо подтверждения identity в local/ci
 *    пишет в лог (`email.logged … link=…`), ссылка берётся из `docker compose logs identity`.
 * 2. A создаёт сборник «Кино · кадры …» (8 фильмов, у двух свои эмодзи): черновик — через
 *    API, восьмая единица — в конструкторе. Две вкладки (два входа A = два устройства):
 *    вторая получает 409 `collections.draft_conflict` и экран выбора, оставляет свою версию
 *    и публикует.
 * 3. B находит сборник поиском («кадры», `film`, «новые»): на странице число единиц, режимы
 *    «N из M», автор, состава нет. B играет 5 раундов `film.collection.frame`; режим эмодзи
 *    принимает «все» (8 раундов) и отклоняет 25.
 * 4. После `GameFinished` у B «пройден» и открыт состав; лайк и избранное идемпотентны,
 *    свой сборник A лайкнуть не может.
 * 5. Из ревила — «Смотреть позже», из итога — «Просмотрено» (единица уходит из «Смотреть
 *    позже», `exclusive_with`); `/library` показывает полки.
 * 6. Админ скрывает сборник: пропадает из каталога, старт сессии — 404, у A — плашка.
 * 7. Гость играет публичный сборник, но лайк и «Сохранить» ведут на регистрацию, API — 403.
 *
 * Ответы тесту неизвестны: раунды завершаются «Сдаться». Селекторы — `data-testid`
 * (контракт с фронтендом перечислен у каждого шага).
 */
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "node:path";
import {
  expect,
  request as playwrightRequest,
  test,
  type APIRequestContext,
  type APIResponse,
  type Browser,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";

test.skip(process.env.E2E_SEEDED !== "1", "стек не засеян: нужен шаг засева (E2E_SEEDED=1)");
test.describe.configure({ mode: "serial" });

const FRAME = "film.collection.frame";
const EMOJI = "film.collection.emoji";
const WATCH_LATER = "system:watch_later";
const WATCHED = "system:watched";
const PASSWORD = "e2e-collections-password-1";
// Уникальный хвост прогона: названия и ники не пересекаются с прошлыми прогонами на том же стеке.
const RUN = randomUUID().slice(0, 8);
const TITLE = `Кино · кадры ${RUN}`;
// Свои эмодзи A: строго RGI, 6..12 графем (GV_COLLECTIONS__LIMITS__EMOJI_MIN|MAX_GRAPHEMES).
const CUSTOM_EMOJI = ["🚀🌌⏳🌽🪐📚", "🚢🧊💑🎨💎🌊"];
// Кураторские сборники засева (seeds/collections/*.yaml).
const CURATED_TITLES = ["Кино · кадры: синтетика и классика", "Кино · эмодзи: угадай по смайлам", "Музыка · интро: рок-классика"];

type Json = Record<string, unknown>;
type Problem = { code: string; details?: Json | null };
type Suggestion = { id: string; title: string; domain: string };
type DraftOut = { collection_id: string; revision: number; body: Json & { title: string; items: Json[] } };
type CollectionMode = { mode_key: string; available: boolean; playable: number; total: number; round_options: (number | string)[] };
type Me = { liked: boolean; favorite: boolean; completed: boolean; is_author: boolean };
type CollectionDetail = {
  collection_id: string;
  title: string;
  status: string;
  version: number;
  items_count: number;
  likes_count: number;
  author: { kind: string; nickname: string | null };
  items_visible: boolean;
  items: { media_item_id: string }[] | null;
  me: Me | null;
};

// ----------------------------------------------------------------------------- API от имени страницы
/** Публичный API сервиса через gateway от имени контекста страницы: cookie + CSRF + Origin. */
function api(page: Page, service: "collections" | "library" | "game" | "catalog" | "identity") {
  const base = `/api/${service}/v1`;
  const headers = async (): Promise<Record<string, string>> => {
    const csrf = (await page.context().cookies()).find((c) => c.name === "gv_csrf")?.value ?? "";
    // Запросы собираются руками — Origin ставим сами (cookie-мутации требуют его вместе с CSRF).
    return { "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin, "Idempotency-Key": randomUUID() };
  };
  return {
    get: (p: string) => page.request.get(`${base}${p}`),
    post: async (p: string, data?: unknown) => page.request.post(`${base}${p}`, { data, headers: await headers() }),
    put: async (p: string, data?: unknown, key?: string) =>
      page.request.put(`${base}${p}`, { data, headers: { ...(await headers()), ...(key ? { "Idempotency-Key": key } : {}) } }),
    delete: async (p: string) => page.request.delete(`${base}${p}`, { headers: await headers() }),
  };
}

// Ответ API-запроса или перехваченный ответ страницы — общие методы.
type AnyResponse = Pick<APIResponse, "status" | "text" | "json" | "url">;

async function json<T>(res: AnyResponse, status: number | number[] = 200): Promise<T> {
  const allowed = Array.isArray(status) ? status : [status];
  expect(allowed, `${res.url()} → ${res.status()}: ${await res.text()}`).toContain(res.status());
  return (await res.json()) as T;
}

async function problem(res: AnyResponse, status: number): Promise<string> {
  expect(res.status(), `${res.url()}: ${await res.text()}`).toBe(status);
  return ((await res.json()) as Problem).code;
}

const isDraftSave = (id: string) => (r: { url(): string; request(): { method(): string } }) =>
  r.request().method() === "PUT" && r.url().endsWith(`/api/collections/v1/collections/${id}/draft`);

// ----------------------------------------------------------------------------- регистрация
const COMPOSE_DIR = process.env.E2E_COMPOSE_DIR;
const COMPOSE_FILES = (
  process.env.E2E_COMPOSE_FILES ??
  "-f compose.infra.yml -f compose.infra.dev.yml -f compose.services.yml -f compose.services.dev.yml -f compose.web.yml -f compose.gateway.yml"
)
  .split(/\s+/)
  .filter(Boolean);

/**
 * Последняя ссылка подтверждения e-mail из лога identity после `since`.
 *
 * В local/ci identity шлёт письма в лог (`LogEmailSender`, ссылка — только вне production);
 * адреса в логе нет, поэтому берём самую свежую ссылку: сценарий последовательный (workers: 1).
 */
function verifyTokenSince(since: string): string | null {
  const cwd = COMPOSE_DIR ?? path.resolve(test.info().project.testDir, "../infra/compose");
  const logs = execFileSync(
    "docker",
    ["compose", "--env-file", ".env", ...COMPOSE_FILES, "logs", "--no-color", "--since", since, "identity"],
    { cwd, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 },
  );
  const tokens = [...logs.matchAll(/verify-email\?token=([A-Za-z0-9_.~%-]+)/g)].map((m) => m[1]!);
  return tokens.at(-1) ?? null;
}

type User = { context: BrowserContext; page: Page; nickname: string };

/** Регистрация через UI → ссылка из письма → вход. Возвращает вошедший контекст. */
async function registerUser(browser: Browser, prefix: string): Promise<User> {
  const nickname = `${prefix}${RUN}`;
  const context = await browser.newContext();
  const page = await context.newPage();
  const since = new Date(Date.now() - 1_000).toISOString();
  await page.goto("/register");
  await page.getByLabel("Ник").fill(nickname);
  await page.getByLabel("E-mail").fill(`${nickname}@example.com`);
  await page.getByLabel("Пароль").fill(PASSWORD);
  for (const box of await page.getByRole("checkbox").all()) await box.check();
  await page.getByRole("button", { name: "Зарегистрироваться" }).click();
  await expect(page.getByTestId("register-done")).toBeVisible();

  let token: string | null = null;
  await expect
    .poll(() => (token = verifyTokenSince(since)), { timeout: 30_000, message: "ссылка подтверждения в логе identity" })
    .not.toBeNull();
  await page.goto(`/verify-email?token=${token}`);
  await expect(page.getByTestId("verify-state")).toContainText("подтверждён");
  await login(page, nickname);
  return { context, page, nickname };
}

async function login(page: Page, nickname: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("E-mail или ник").fill(nickname);
  await page.getByLabel("Пароль").fill(PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByTestId("user-name")).toHaveText(nickname);
}

// ----------------------------------------------------------------------------- игра
function isMove(url: string, method: string): boolean {
  return method === "POST" && /\/api\/game\/v1\/sessions\/[^/]+\/(guesses|skips|rounds\/\d+\/give-up)$/.test(url);
}

/** Сдать текущий раунд; вернуть `media_item_id` ревила из ответа сервера. */
async function giveUpRound(page: Page): Promise<string> {
  const [move] = await Promise.all([
    page.waitForResponse((r) => isMove(r.url(), r.request().method())),
    page.locator("[data-testid=give-up]:enabled").click(),
  ]);
  const result = (await move.json()) as { round: { reveal: { media_item_id: string } | null } };
  expect(result.round.reveal?.media_item_id).toBeTruthy();
  return result.round.reveal!.media_item_id;
}

/** Доиграть сдачами до итога (`final-score`). */
async function playUntilResults(page: Page, maxSteps = 40): Promise<void> {
  const next = page.getByTestId("reveal-next");
  const done = page.getByTestId("final-score");
  const giveUp = page.locator("[data-testid=give-up]:enabled");
  for (let step = 0; step < maxSteps; step++) {
    await expect(done.or(next).or(giveUp).first()).toBeVisible();
    if (await done.isVisible()) return;
    if (await next.isVisible()) {
      await next.click();
      continue;
    }
    await giveUpRound(page);
  }
  throw new Error(`сессия не дошла до итога за ${maxSteps} шагов`);
}

/** Страница сборника → режим → 5 раундов → «Играть» (`/play/s/<id>`). */
async function startFromCollectionPage(page: Page, collectionId: string, modeKey: string): Promise<string> {
  await page.goto(`/collections/${collectionId}`);
  await page.getByTestId(`collection-mode-${modeKey}`).click();
  await page.getByTestId("collection-rounds-5").click();
  await page.getByTestId("collection-play").click();
  await expect(page).toHaveURL(/\/play\/s\//);
  return page.url().split("/").pop()!;
}

/** «Сохранить → полка» (`save-button` → `shelf-option-<ref>`), ждём ответ library. */
async function saveTo(page: Page, scope: Locator, ref: string, itemId: string) {
  await scope.getByTestId("save-button").click();
  const [res] = await Promise.all([
    page.waitForResponse(
      (r) => r.request().method() === "PUT" && r.url().endsWith(`/api/library/v1/shelves/${ref}/items/${itemId}`),
    ),
    page.getByTestId(`shelf-option-${ref}`).click(),
  ]);
  expect(res.status(), await res.text()).toBe(200);
  return (await res.json()) as { changed: boolean; shelves: string[]; removed_from: string[] };
}

// ----------------------------------------------------------------------------- админ
const ADMIN = {
  login: process.env.GV_BOOTSTRAP_ADMIN__LOGIN,
  password: process.env.GV_BOOTSTRAP_ADMIN__PASSWORD,
  secret: process.env.GV_SERVICE_SECRET_ADMIN,
  identity: process.env.GV_SEED__IDENTITY_URL ?? "http://127.0.0.1:8100",
  media: process.env.GV_SEED__MEDIA_URL ?? "http://127.0.0.1:8102",
  collections: process.env.GV_SEED__COLLECTIONS_URL ?? "http://127.0.0.1:8106",
};
const HAS_ADMIN = Boolean(ADMIN.login && ADMIN.password && ADMIN.secret);

/** admin-токен как у `gv dev seed`: сервисный токен клиента `admin` → вход в админку. */
async function adminContext(baseURL: string): Promise<APIRequestContext> {
  const identity = await playwrightRequest.newContext({ baseURL: ADMIN.identity });
  try {
    const svc = await json<{ access_token: string }>(
      await identity.post("/internal/v1/service-tokens", {
        data: { client_id: "admin", client_secret: ADMIN.secret, audience: "identity" },
      }),
    );
    const session = await json<{ access_token: string }>(
      await identity.post("/internal/v1/admin-sessions", {
        headers: { Authorization: `Bearer ${svc.access_token}` },
        data: { login: ADMIN.login, password: ADMIN.password },
      }),
    );
    return playwrightRequest.newContext({ baseURL, extraHTTPHeaders: { Authorization: `Bearer ${session.access_token}` } });
  } finally {
    await identity.dispose();
  }
}

// ----------------------------------------------------------------------------- состояние сценария
let author: User;
let player: User;
let collectionId = "";
let films: Suggestion[] = [];
let savedItem = "";

test.beforeAll(async ({ browser }) => {
  test.setTimeout(180_000);
  author = await registerUser(browser, "e2eA");
  player = await registerUser(browser, "e2eB");
});

test.afterAll(async () => {
  await author?.context.close();
  await player?.context.close();
});

// ----------------------------------------------------------------------------- 1. засев
test("засев: кураторские сборники в каталоге", async ({ page }) => {
  await page.goto("/");
  const res = await api(page, "collections").get("/collections?curated=true&sort=new&limit=50");
  const titles = (await json<{ items: { title: string; kind: string; author: { kind: string } }[] }>(res)).items;
  for (const title of CURATED_TITLES) {
    const card = titles.find((c) => c.title === title);
    expect(card, title).toBeDefined();
    expect(card!.kind).toBe("curated");
    expect(card!.author.kind).toBe("curator");
  }
});

// ----------------------------------------------------------------------------- 2. конструктор
test("A: черновик, конфликт двух вкладок, публикация", async ({ browser }) => {
  test.setTimeout(180_000);
  const a = api(author.page, "collections");

  // Единицы — синтетика 3a (у всех готовы кадры): подсказки каталога по префиксу названия.
  const found = await json<{ items: Suggestion[] }>(
    await api(author.page, "catalog").get(`/autocomplete?q=${encodeURIComponent("Синтетика")}&domain=film&limit=50`),
  );
  films = found.items.filter((s) => s.domain === "film").slice(0, 8);
  expect(films).toHaveLength(8);

  const created = await json<DraftOut>(await a.post("/collections", { domain: "film", title: TITLE }), 201);
  collectionId = created.collection_id;
  const body = {
    title: TITLE,
    description: "Сборник e2e: фильмы по кадру.",
    tags: ["classic"],
    cover: { preset: "cinema" },
    default_mode_key: FRAME,
    visibility: "public",
    // Семь единиц — сразу (у двух свои эмодзи), восьмую A добавит в конструкторе.
    items: films.slice(0, 7).map((f, i) => ({ media_item_id: f.id, custom: CUSTOM_EMOJI[i] ? { emoji: CUSTOM_EMOJI[i] } : null })),
  };
  const draft = await json<DraftOut>(
    await a.put(`/collections/${collectionId}/draft`, { base_revision: created.revision, client_label: "e2e api", body }),
  );
  expect(draft.revision).toBeGreaterThan(created.revision);

  // Вторая вкладка — второй вход A (другое устройство).
  const second = await browser.newContext();
  try {
    const tab2 = await second.newPage();
    await login(tab2, author.nickname);
    const tab1 = author.page;
    // Обе вкладки загрузили одну ревизию черновика (`editor-title` заполнен).
    for (const tab of [tab1, tab2]) {
      await tab.goto(`/collections/${collectionId}/edit`);
      await expect(tab.getByTestId("editor-title")).toHaveValue(TITLE);
    }

    // Вкладка 1 меняет название — автосохранение (debounce) проходит.
    const [saved1] = await Promise.all([
      tab1.waitForResponse(isDraftSave(collectionId)),
      tab1.getByTestId("editor-title").fill(`${TITLE} (вкладка 1)`),
    ]);
    expect(saved1.status(), await saved1.text()).toBe(200);

    // Вкладка 2 на старой ревизии добавляет восьмую единицу поиском — 409 и экран выбора.
    const eighth = films[7]!;
    await tab2.getByTestId("editor-item-search").fill(eighth.title);
    const [conflict] = await Promise.all([
      tab2.waitForResponse(isDraftSave(collectionId)),
      tab2.getByTestId(`editor-add-${eighth.id}`).click(),
    ]);
    expect(await problem(conflict, 409)).toBe("collections.draft_conflict");
    await expect(tab2.getByTestId("draft-conflict")).toBeVisible();

    // «Моя версия»: сохранение поверх актуальной ревизии.
    const [kept] = await Promise.all([tab2.waitForResponse(isDraftSave(collectionId)), tab2.getByTestId("draft-keep-mine").click()]);
    expect(kept.status(), await kept.text()).toBe(200);
    await expect(tab2.getByTestId("draft-conflict")).toBeHidden();
    const current = await json<DraftOut>(await a.get(`/collections/${collectionId}/draft`));
    expect(current.body.title).toBe(TITLE);
    expect(current.body.items).toHaveLength(8);

    // Публикация из второй вкладки.
    const [published] = await Promise.all([
      tab2.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith(`/collections/${collectionId}/publish`)),
      tab2.getByTestId("editor-publish").click(),
    ]);
    expect(published.status(), await published.text()).toBe(200);
  } finally {
    await second.close();
  }

  const detail = await json<CollectionDetail>(await a.get(`/collections/${collectionId}`));
  expect(detail.status).toBe("published");
  expect(detail.version).toBe(1);
  expect(detail.me?.is_author).toBe(true);
  // Автор видит свой состав.
  expect(detail.items).toHaveLength(8);
  // Свой сборник лайкнуть нельзя.
  expect(await problem(await a.put(`/collections/${collectionId}/like`), 403)).toBe("collections.own_collection");

  // game проецирует CollectionPublished: режим кадров доступен, играбельны все 8.
  await expect
    .poll(
      async () => {
        const res = await api(author.page, "game").get(`/collections/${collectionId}/modes`);
        if (res.status() !== 200) return `HTTP ${res.status()}`;
        const mode = ((await res.json()) as CollectionMode[]).find((m) => m.mode_key === FRAME);
        return mode ? `${mode.available}:${mode.playable}/${mode.total}` : "нет режима";
      },
      { timeout: 60_000, message: "сборник в проекции game" },
    )
    .toBe("true:8/8");
});

test("A: свои эмодзи — наборы media-pipeline со scope сборника (AssetsReady)", async () => {
  test.skip(!HAS_ADMIN, "нет учётных данных админа (GV_BOOTSTRAP_ADMIN__*, GV_SERVICE_SECRET_ADMIN)");
  const media = await adminContext(ADMIN.media);
  try {
    for (const film of films.slice(0, CUSTOM_EMOJI.length)) {
      await expect
        .poll(
          async () => {
            const sets = await json<{ status: string; scope: { kind: string; id: string } | null }[]>(
              await media.get(`/admin/v1/asset-sets?media_item_id=${film.id}`),
            );
            return sets.filter((s) => s.scope?.kind === "collection" && s.scope.id === collectionId).map((s) => s.status);
          },
          { timeout: 90_000, message: `набор своих эмодзи ${film.title}` },
        )
        .toEqual(["ready"]);
    }
  } finally {
    await media.dispose();
  }
});

// ----------------------------------------------------------------------------- 3. поиск и игра B
test("B: поиск сборника, страница без состава, 5 раундов, сохранение из ревила и итога", async () => {
  test.setTimeout(180_000);
  const page = player.page;

  // Каталог: фильтры и сортировка — из адреса, поиск — поле `collection-search`.
  await page.goto("/collections?domain=film&sort=new");
  await page.getByTestId("collection-search").fill("кадры");
  const card = page.getByTestId(`collection-card-${collectionId}`);
  await expect(card).toBeVisible();
  await card.click();
  await expect(page).toHaveURL(new RegExp(`/collections/${collectionId}$`));

  // Страница: число единиц, автор, режим «8 из 8»; состава до прохождения нет (D6).
  await expect(page.getByTestId("collection-items-count")).toContainText("8");
  await expect(page.getByTestId("collection-author")).toContainText(author.nickname);
  await expect(page.getByTestId(`collection-mode-${FRAME}`)).toContainText(/8\D+8/);
  await expect(page.getByTestId("collection-items")).toHaveCount(0);
  const before = await json<CollectionDetail>(await api(page, "collections").get(`/collections/${collectionId}`));
  expect(before.items).toBeNull();
  expect(before.me?.completed).toBe(false);

  // Игра: 5 раундов кадров. До ответа в снимке нет media_item_id.
  const sessionId = await startFromCollectionPage(page, collectionId, FRAME);
  const snap = await json<{ rounds: unknown[] }>(await api(page, "game").get(`/sessions/${sessionId}`));
  expect(snap.rounds).toHaveLength(5);
  expect(JSON.stringify(snap)).not.toMatch(/media_item_id/);

  // Ревил первого раунда: «Сохранить → Смотреть позже».
  savedItem = await giveUpRound(page);
  const reveal = page.getByTestId("reveal");
  const later = await saveTo(page, reveal, WATCH_LATER, savedItem);
  expect(later.shelves).toContain(WATCH_LATER);
  await page.keyboard.press("Escape");

  await playUntilResults(page);
  await expect(page).toHaveURL(new RegExp(`/results/${sessionId}`));
  await expect(page.getByTestId("result-row")).toHaveCount(5);
  const result = await json<{ collection: { id: string; version: number } | null }>(
    await api(page, "game").get(`/sessions/${sessionId}/result`),
  );
  expect(result.collection).toEqual({ id: collectionId, version: 1 });

  // Итог: «Сохранить → Просмотрено» убирает единицу из «Смотреть позже» (exclusive_with).
  const firstRow = page.getByTestId("result-row").first();
  const watched = await saveTo(page, firstRow, WATCHED, savedItem);
  expect(watched.removed_from).toContain(WATCH_LATER);
  await expect(page.getByTestId(`shelf-option-${WATCHED}`)).toHaveAttribute("aria-checked", "true");
  await expect(page.getByTestId(`shelf-option-${WATCH_LATER}`)).toHaveAttribute("aria-checked", "false");
  await page.keyboard.press("Escape");
  const shelves = await json<{ shelves: string[] }>(await api(page, "library").get(`/items/${savedItem}/shelves`));
  expect(shelves.shelves).toContain(WATCHED);
  expect(shelves.shelves).not.toContain(WATCH_LATER);
});

test("B: режим эмодзи принимает «все» (8 раундов) и отклоняет 25", async () => {
  const game = api(player.page, "game");
  await expect
    .poll(async () => {
      const modes = await json<CollectionMode[]>(await game.get(`/collections/${collectionId}/modes`));
      return modes.find((m) => m.mode_key === EMOJI)?.available ?? false;
    })
    .toBe(true);
  const rejected = await game.post("/sessions", { mode_key: EMOJI, collection_id: collectionId, rounds: 25 });
  expect(await problem(rejected, 422)).toBe("game.rounds_not_allowed");
  const all = await json<{ rounds: unknown[] }>(
    await game.post("/sessions", { mode_key: EMOJI, collection_id: collectionId, rounds: "all" }),
    201,
  );
  expect(all.rounds).toHaveLength(8);
  expect(JSON.stringify(all)).not.toMatch(/media_item_id/);
});

// ----------------------------------------------------------------------------- 4. «пройден», лайк, избранное
test("B: «пройден» открывает состав; лайк и избранное идемпотентны", async () => {
  const page = player.page;
  const c = api(page, "collections");
  // collections отмечает прохождение по GameFinished (consumer) — ждём.
  await expect
    .poll(async () => (await json<CollectionDetail>(await c.get(`/collections/${collectionId}`))).me?.completed ?? false, {
      timeout: 60_000,
      message: "отметка «пройден» после GameFinished",
    })
    .toBe(true);

  await page.goto(`/collections/${collectionId}`);
  await expect(page.getByTestId("collection-completed")).toBeVisible();
  await expect(page.getByTestId("collection-items").locator("[data-testid^=collection-entry-]")).toHaveCount(8);
  await expect(page.getByTestId(`collection-entry-${savedItem}`)).toBeVisible();

  // Лайк из UI: оптимистично, затем подтверждён сервером.
  const like = page.getByTestId("collection-like");
  const [liked] = await Promise.all([
    page.waitForResponse((r) => r.request().method() === "PUT" && r.url().endsWith(`/collections/${collectionId}/like`)),
    like.click(),
  ]);
  expect(liked.status(), await liked.text()).toBe(200);
  await expect(like).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("collection-likes-count")).toHaveText("1");
  // Повтор — тот же счётчик (D13).
  expect(await json<{ liked: boolean; likes_count: number }>(await c.put(`/collections/${collectionId}/like`))).toEqual({
    liked: true,
    likes_count: 1,
  });

  const favorite = page.getByTestId("collection-favorite");
  const [fav] = await Promise.all([
    page.waitForResponse((r) => r.request().method() === "PUT" && r.url().endsWith(`/collections/${collectionId}/favorite`)),
    favorite.click(),
  ]);
  expect(fav.status(), await fav.text()).toBe(200);
  await expect(favorite).toHaveAttribute("aria-pressed", "true");
  expect(await json<{ favorite: boolean }>(await c.put(`/collections/${collectionId}/favorite`))).toEqual({ favorite: true });
  const favorites = await json<{ items: { collection_id: string }[] }>(await c.get("/me/favorites"));
  expect(favorites.items.map((f) => f.collection_id)).toContain(collectionId);

  // После перезагрузки состояние — с сервера.
  await page.reload();
  await expect(page.getByTestId("collection-like")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("collection-favorite")).toHaveAttribute("aria-pressed", "true");
});

// ----------------------------------------------------------------------------- 5. медиатека
test("B: /library — полки, «Просмотрено» с единицей, «Смотреть позже» без неё", async () => {
  const page = player.page;
  await page.goto("/library");
  await expect(page.getByTestId(`shelf-card-${WATCH_LATER}`)).toBeVisible();
  const watchedCard = page.getByTestId(`shelf-card-${WATCHED}`);
  await expect(watchedCard).toContainText("1");
  await watchedCard.click();
  await expect(page).toHaveURL(new RegExp(`/library/${WATCHED}$`));
  await expect(page.getByTestId(`library-item-${savedItem}`)).toBeVisible();

  await page.goto(`/library/${WATCH_LATER}`);
  await expect(page.getByTestId(`library-item-${savedItem}`)).toHaveCount(0);
});

// ----------------------------------------------------------------------------- 7. гость
test("гость: играет публичный сборник, лайк и «Сохранить» ведут на регистрацию, API — 403", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/");
  // Гостевая сессия (cookie + CSRF): 409 — клиент уже завёл гостя при загрузке.
  const guest = await page.request.post("/api/identity/v1/auth/guest", { headers: { Origin: new URL(page.url()).origin } });
  expect([204, 409]).toContain(guest.status());

  const like = await api(page, "collections").put(`/collections/${collectionId}/like`);
  expect(await problem(like, 403)).toBe("auth.guest_forbidden");
  const save = await api(page, "library").put(`/shelves/${WATCH_LATER}/items/${savedItem}`, { source: "reveal" });
  expect(await problem(save, 403)).toBe("auth.guest_forbidden");
  const create = await api(page, "collections").post("/collections", { domain: "film", title: "Гостевой сборник" });
  expect(await problem(create, 403)).toBe("auth.guest_forbidden");

  const sessionId = await startFromCollectionPage(page, collectionId, FRAME);
  await giveUpRound(page);
  // «Сохранить» гостя — ссылка на регистрацию.
  await expect(page.getByTestId("reveal").getByTestId("save-button")).toHaveAttribute("href", /\/register/);
  await playUntilResults(page);
  await expect(page).toHaveURL(new RegExp(`/results/${sessionId}`));
  await expect(page.getByTestId("result-row")).toHaveCount(5);

  // Лайк на странице сборника — тоже на регистрацию; гостю «пройден» не ставится.
  await page.goto(`/collections/${collectionId}`);
  await page.getByTestId("collection-like").click();
  await expect(page).toHaveURL(/\/register/);
  const detail = await json<CollectionDetail>(await api(page, "collections").get(`/collections/${collectionId}`));
  expect(detail.me).toBeNull();
  expect(detail.items).toBeNull();
});

// ----------------------------------------------------------------------------- 6. модерация
test("админ скрывает сборник: нет в каталоге, старт — 404, у автора плашка", async () => {
  test.skip(!HAS_ADMIN, "нет учётных данных админа (GV_BOOTSTRAP_ADMIN__*, GV_SERVICE_SECRET_ADMIN)");
  const admin = await adminContext(ADMIN.collections);
  try {
    const hidden = await json<{ status: string; hidden: { reason: string } | null }>(
      await admin.post(`/admin/v1/collections/${collectionId}/hide`, {
        headers: { "Idempotency-Key": randomUUID() },
        data: { reason: "e2e: проверка скрытия" },
      }),
    );
    expect(hidden.status).toBe("hidden");
    expect(hidden.hidden?.reason).toBe("e2e: проверка скрытия");
  } finally {
    await admin.dispose();
  }

  const page = player.page;
  const search = await json<{ items: { collection_id: string }[] }>(
    await api(page, "collections").get(`/collections?q=${encodeURIComponent("кадры")}&domain=film&sort=new&limit=50`),
  );
  expect(search.items.map((c) => c.collection_id)).not.toContain(collectionId);
  // game получает CollectionUpdated(status=hidden): режимы сборника и старт сессии — 404.
  // Опрашиваем чтение (старт в цикле плодил бы сессии и упирался в лимит).
  const game = api(page, "game");
  await expect
    .poll(async () => (await game.get(`/collections/${collectionId}/modes`)).status(), {
      timeout: 60_000,
      message: "скрытый сборник в проекции game",
    })
    .toBe(404);
  const start = await game.post("/sessions", { mode_key: FRAME, collection_id: collectionId, rounds: 5 });
  expect(await problem(start, 404)).toBe("game.collection_not_found");

  // Автор видит плашку «скрыт модератором» в «Моих сборниках».
  await author.page.goto("/me/collections");
  await expect(author.page.getByTestId(`my-collection-${collectionId}`).getByTestId("my-collection-hidden")).toBeVisible();
});
