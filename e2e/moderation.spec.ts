/**
 * Полный стек: комментарии, жалобы, очередь модерации, санкции и конфиг social 5c
 * (ADR 0050-0053).
 *
 * Один последовательный сценарий на засеянном стеке (`gv dev seed`: кураторские сборники
 * публичны, конфиг social опубликован bootstrap-задачей, порог жалоб в сиде — 3). Игроки
 * A, B, C, D, E регистрируются через UI; админ-контекст — токен как у `gv dev seed`, прямой адрес
 * admin-API social (`GV_SEED__SOCIAL_URL`, по умолчанию 127.0.0.1:8111: через gateway admin-ручки
 * не проброшены).
 * 1. A пишет комментарий под кураторским сборником; B, C, D жалуются -> кейс в очереди ->
 *    модератор берёт в работу и решает «удалить контент» -> комментарий пропал, заявителям
 *    пришло `report_resolved`.
 * 2. Конфиг: админ ставит порог жалоб 1 (черновик + публикация) -> одной жалобы хватает для
 *    кейса; порог возвращается в finally.
 * 3. Санкция `online` игроку A: баннер и `/sanctions`, создание комнаты — 403
 *    `match.online_banned`, после снятия снова можно.
 * 4. Санкция `account` игроку E: вход — 403 `auth.account_banned`.
 *
 * Доставка асинхронная (outbox -> Redpanda -> consumers): везде опрос состояния с таймаутом.
 */
import { randomUUID } from "node:crypto";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { ADMIN, HAS_ADMIN, NO_ADMIN_REASON, adminContext, userIdOf } from "./admin";
import { PASSWORD, api, apiErrorsOf, closePlayers, json, registerPlayers, RUN, type Player } from "./online";

test.skip(process.env.E2E_SEEDED !== "1", "стек не засеян: нужен шаг засева (E2E_SEEDED=1)");
test.skip(!HAS_ADMIN, NO_ADMIN_REASON);
test.describe.configure({ mode: "serial", timeout: 240_000 });

const CURATED = "Кино · кадры: синтетика и классика"; // seeds/collections
const DUEL = "music.duel.classic";
const DELIVERY_TIMEOUT_MS = 90_000;
const PROJECTION_TIMEOUT_MS = 60_000;
const CONFIG_CACHE_WAIT_MS = 6_000; // GV_SOCIAL__CONFIG_CACHE_TTL_S по умолчанию 5 с

type CaseSummary = {
  id: string;
  target_kind: string;
  target_id: string;
  status: string;
  reports_count: number;
  version: number;
};
type Notice = { id: string; type: string };
type Comment = { id: string; body: string };
type ConfigBody = { card: { version: number; status: string; body_sha256: string }; config: Record<string, any> };

let a: Player;
let b: Player;
let c: Player;
let d: Player;
let e: Player;
let idA = "";
let idE = "";
let collectionId = "";
let social: APIRequestContext;

test.beforeAll(async ({ browser }) => {
  test.setTimeout(300_000);
  [a, b, c, d, e] = (await registerPlayers(browser, ["moa", "mob", "moc", "mod", "moe"])) as [
    Player,
    Player,
    Player,
    Player,
    Player,
  ];
  social = await adminContext(ADMIN.social);
  idA = await userIdOf(a.page);
  idE = await userIdOf(e.page);
  const list = await json<{ items: { collection_id: string; title: string }[] }>(
    await api(a.page, "collections").get("/collections?curated=true&sort=new&limit=50"),
  );
  const hit = list.items.find((i) => i.title === CURATED);
  expect(hit, `кураторский сборник «${CURATED}» из сидов`).toBeTruthy();
  collectionId = hit!.collection_id;
});

test.afterAll(async () => {
  await social?.dispose();
  await closePlayers([a, b, c, d, e].filter(Boolean));
});

test.afterEach(async ({}, testInfo) => {
  if (a && testInfo.status !== testInfo.expectedStatus)
    await testInfo.attach("api-errors", { body: apiErrorsOf([a, b, c, d, e].map((p) => p.page)) });
});

// ----------------------------------------------------------------------------- помощники
const idem = () => ({ "Idempotency-Key": randomUUID() });

/** Комментарий под сборником через UI; вернуть его id (из API social). */
async function postComment(player: Player, text: string): Promise<string> {
  const { page } = player;
  await page.goto(`/collections/${collectionId}`);
  await page.getByTestId("comment-text").fill(text);
  await page.getByTestId("comment-submit").click();
  await expect(page.getByTestId("comment").filter({ hasText: text })).toBeVisible();
  const list = await json<{ items: Comment[] }>(
    await api(page, "social").get(`/collections/${collectionId}/comments?limit=50`),
  );
  const hit = list.items.find((i) => i.body === text);
  expect(hit, "комментарий в API social").toBeTruthy();
  return hit!.id;
}

/** Жалоба на комментарий через диалог: причина «Спам или реклама». */
async function reportComment(player: Player, text: string): Promise<void> {
  const { page } = player;
  await page.goto(`/collections/${collectionId}`);
  const comment = page.getByTestId("comment").filter({ hasText: text });
  await comment.getByTestId("comment-report").click();
  await expect(page.getByTestId("report-dialog")).toBeVisible();
  await page.getByTestId("report-reason-spam").check();
  await page.getByTestId("report-submit").click();
  await expect(page.getByTestId("report-done")).toBeVisible();
}

async function findCase(targetId: string): Promise<CaseSummary> {
  let found: CaseSummary | undefined;
  await expect
    .poll(
      async () => {
        const page = await json<{ items: CaseSummary[] }>(
          await social.get("/admin/v1/cases?status=open&target_kind=comment&limit=100"),
        );
        found = page.items.find((i) => i.target_id === targetId);
        return found !== undefined;
      },
      { timeout: DELIVERY_TIMEOUT_MS, message: "кейс жалобы в очереди модерации" },
    )
    .toBe(true);
  return found!;
}

/** Взять кейс в работу и решить; вернуть итоговую карточку. */
async function claimAndResolve(
  item: CaseSummary,
  decision: "dismissed" | "content_removed",
  reason: string,
): Promise<{ status: string; decision: string | null }> {
  const claimed = await json<CaseSummary>(
    await social.post(`/admin/v1/cases/${item.id}/claim`, {
      headers: idem(),
      data: { version: item.version },
    }),
  );
  expect(claimed.status).toBe("in_work");
  return json(
    await social.post(`/admin/v1/cases/${item.id}/resolve`, {
      headers: idem(),
      data: { version: claimed.version, decision, reason },
    }),
  );
}

async function noticeTypes(page: Page): Promise<string[]> {
  const list = await json<{ items: Notice[] }>(
    await api(page, "notifications").get("/notifications?limit=50"),
  );
  return list.items.map((n) => n.type);
}

/** Опубликованный конфиг social: `[версия, тело]`. */
async function publishedConfig(): Promise<[number, Record<string, any>]> {
  const versions = await json<{ items: { version: number; status: string }[] }>(
    await social.get("/admin/v1/config/versions"),
  );
  const current = versions.items.find((v) => v.status === "published");
  expect(current, "опубликованная версия конфига social").toBeTruthy();
  const body = await json<ConfigBody>(await social.get(`/admin/v1/config/versions/${current!.version}`));
  return [current!.version, body.config];
}

/** Черновик с порогом жалоб `threshold` и его публикация. */
async function setThreshold(threshold: number, reason: string): Promise<void> {
  const [, config] = await publishedConfig();
  const next = { ...config, reports: { ...config.reports, threshold_per_day: threshold } };
  const draft = await json<ConfigBody>(
    await social.put("/admin/v1/config/draft", { headers: idem(), data: { config: next, reason } }),
  );
  await json(
    await social.post("/admin/v1/config/draft/publish", {
      headers: idem(),
      data: {
        version: draft.card.version,
        body_sha256: draft.card.body_sha256,
        reason,
      },
    }),
  );
  // Пауза осознанна: кэш конфига в процессе social сбрасывается при публикации, но другие процессы ждут TTL.
  await a.page.waitForTimeout(CONFIG_CACHE_WAIT_MS);
}

/** Выдать санкцию игроку; вернуть id записи. */
async function grant(userId: string, kind: string, reason: string): Promise<string> {
  const res = await social.post(`/admin/v1/users/${userId}/sanctions`, {
    headers: idem(),
    data: { kind, reason, duration_s: 3600 },
  });
  return (await json<{ id: string; active: boolean }>(res, 201)).id;
}

async function revoke(sanctionId: string, reason: string): Promise<void> {
  await json(
    await social.post(`/admin/v1/sanctions/${sanctionId}/revoke`, {
      headers: idem(),
      data: { reason },
    }),
  );
}

/** Попытка создать комнату: код проблемы или `created` (тогда комната сразу закрывается). */
async function tryCreateRoom(player: Player): Promise<string> {
  const res = await api(player.page, "match").post("/rooms", { mode_key: DUEL });
  if (res.status() === 201) {
    const room = (await res.json()) as { code: string };
    await api(player.page, "match").post(`/rooms/${room.code}/leave`);
    return "created";
  }
  return ((await res.json()) as { code: string }).code;
}

// ----------------------------------------------------------------------------- сценарии
test("жалобы B, C, D на комментарий A открывают кейс; «удалить контент» убирает комментарий", async () => {
  const text = `Рекламный комментарий ${RUN}`;
  const commentId = await postComment(a, text);

  for (const reporter of [b, c, d]) await reportComment(reporter, text);

  const item = await findCase(commentId);
  expect(item.reports_count).toBeGreaterThanOrEqual(3);
  const resolved = await claimAndResolve(item, "content_removed", `e2e: удаление ${RUN}`);
  expect(resolved.status).toBe("resolved");
  expect(resolved.decision).toBe("content_removed");

  await a.page.goto(`/collections/${collectionId}`);
  await expect(a.page.getByTestId("comments")).toBeVisible();
  await expect(a.page.getByTestId("comment").filter({ hasText: text })).toHaveCount(0);

  for (const reporter of [b, c, d]) {
    await expect
      .poll(async () => (await noticeTypes(reporter.page)).includes("report_resolved"), {
        timeout: DELIVERY_TIMEOUT_MS,
        message: "уведомление report_resolved заявителю",
      })
      .toBe(true);
  }
});

test("конфиг: порог жалоб 1 — следующему объекту хватает одной жалобы", async () => {
  await setThreshold(1, `e2e: порог 1 ${RUN}`);
  try {
    const text = `Второй комментарий ${RUN}`;
    const commentId = await postComment(a, text);
    await reportComment(b, text);

    const item = await findCase(commentId);
    expect(item.reports_count).toBe(1);
    const resolved = await claimAndResolve(item, "dismissed", `e2e: без нарушений ${RUN}`);
    expect(resolved.decision).toBe("dismissed");
  } finally {
    await setThreshold(3, `e2e: порог 3 (возврат) ${RUN}`);
  }
  const [, config] = await publishedConfig();
  expect(config.reports.threshold_per_day).toBe(3);
});

test("санкция online: баннер, отказ создать комнату, после снятия — можно", async () => {
  const sanctionId = await grant(idA, "online", `e2e: онлайн-бан ${RUN}`);
  let revoked = false;
  try {
    await a.page.goto("/sanctions");
    await expect(a.page.getByTestId("sanctions-banner")).toBeVisible({ timeout: 20_000 });
    const entry = a.page.getByTestId("sanction-online");
    await expect(entry).toBeVisible();
    await expect(entry.getByTestId("sanction-term")).toBeVisible();

    // Проекция санкций в match обновляется асинхронно (<= 20 с обычно, запас — минута).
    await expect
      .poll(() => tryCreateRoom(a), {
        timeout: PROJECTION_TIMEOUT_MS,
        message: "match отказывает в комнате: match.online_banned",
      })
      .toBe("match.online_banned");

    await revoke(sanctionId, `e2e: снятие ${RUN}`);
    revoked = true;
    await expect
      .poll(() => tryCreateRoom(a), {
        timeout: PROJECTION_TIMEOUT_MS,
        message: "после снятия санкции комната создаётся",
      })
      .toBe("created");

    await a.page.goto("/sanctions");
    await expect(a.page.getByTestId("sanctions-empty")).toBeVisible();
    await expect(a.page.getByTestId("sanctions-banner")).toHaveCount(0);
  } finally {
    if (!revoked) await revoke(sanctionId, `e2e: снятие (уборка) ${RUN}`);
  }
});

test("санкция account: вход отвечает 403 «Аккаунт заблокирован»", async () => {
  const sanctionId = await grant(idE, "account", `e2e: бан аккаунта ${RUN}`);
  try {
    // Вход с этого же контекста (устройство известно): сессия не открывается -> 403 auth.account_banned.
    await expect
      .poll(
        async () => {
          const res = await e.page.request.post("/api/identity/v1/auth/login", {
            data: { login: e.nickname, password: PASSWORD },
          });
          return res.status() === 403
            ? ((await res.json()) as { code: string }).code
            : `${res.status()}`;
        },
        { timeout: PROJECTION_TIMEOUT_MS, message: "вход заблокированного аккаунта" },
      )
      .toBe("auth.account_banned");
  } finally {
    await revoke(sanctionId, `e2e: снятие бана аккаунта ${RUN}`);
  }
});
