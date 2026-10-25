/**
 * Админ-контекст для сквозных спеков (`collections`, `shop`, `notifications`, `events`, `moderation`): токен как у
 * `gv dev seed` (сервисный токен клиента `admin` -> вход в админку) и прямые адреса admin-API
 * сервисов. Адреса и переменные те же, что у `gv dev seed`
 * (GV_SEED__*_URL, GV_BOOTSTRAP_ADMIN__*, GV_SERVICE_SECRET_ADMIN).
 */
import { randomUUID } from "node:crypto";
import zlib from "node:zlib";
import {
  expect,
  request as playwrightRequest,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { json } from "./online";

export const ADMIN = {
  login: process.env.GV_BOOTSTRAP_ADMIN__LOGIN,
  password: process.env.GV_BOOTSTRAP_ADMIN__PASSWORD,
  secret: process.env.GV_SERVICE_SECRET_ADMIN,
  identity: process.env.GV_SEED__IDENTITY_URL ?? "http://127.0.0.1:8100",
  media: process.env.GV_SEED__MEDIA_URL ?? "http://127.0.0.1:8102",
  progression: process.env.GV_SEED__PROGRESSION_URL ?? "http://127.0.0.1:8109",
  social: process.env.GV_SEED__SOCIAL_URL ?? "http://127.0.0.1:8111",
  collections: process.env.GV_SEED__COLLECTIONS_URL ?? "http://127.0.0.1:8106",
};
export const HAS_ADMIN = Boolean(ADMIN.login && ADMIN.password && ADMIN.secret);
export const NO_ADMIN_REASON =
  "нет учётных данных админа (GV_BOOTSTRAP_ADMIN__*, GV_SERVICE_SECRET_ADMIN)";

/** admin-токен как у `gv dev seed`: сервисный токен клиента `admin` -> вход в админку. */
export async function adminContext(baseURL: string): Promise<APIRequestContext> {
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
    return playwrightRequest.newContext({
      baseURL,
      extraHTTPHeaders: { Authorization: `Bearer ${session.access_token}` },
    });
  } finally {
    await identity.dispose();
  }
}

/** Идентификатор вошедшего игрока (`GET /api/identity/v1/me`). */
export async function userIdOf(page: Page): Promise<string> {
  const me = await json<{ id: string }>(await page.request.get("/api/identity/v1/me"));
  return me.id;
}

/**
 * Начислить игроку Версы (бакет `earned`) через admin-API progression. Проекция пользователя в
 * progression появляется асинхронно (событие регистрации): 404 `progression.user_not_found`
 * повторяем опросом; ключ идемпотентности один, повтор не задвоит выдачу.
 */
export async function grantVerses(
  admin: APIRequestContext,
  userId: string,
  amount: number,
  reason: string,
): Promise<void> {
  const key = randomUUID();
  await expect
    .poll(
      async () => {
        const res = await admin.post(`/admin/v1/users/${userId}/ledger/grant`, {
          headers: { "Idempotency-Key": key },
          data: { amount, bucket: "earned", reason },
        });
        return res.status() === 201 ? 201 : `${res.status()} ${await res.text()}`;
      },
      { timeout: 60_000, message: "выдача Версов админом (ждём проекцию игрока в progression)" },
    )
    .toBe(201);
}

/** Валидный PNG `size`×`size` (по умолчанию 160: min_side_px аватарки 128) (RGB, градиент с `seed`): без внешних файлов. */
export function makePng(size = 160, seed = 0): Buffer {
  const table = Array.from({ length: 256 }, (_, n) => {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    return c >>> 0;
  });
  const crc = (buf: Buffer): number => {
    let c = 0xffffffff;
    for (const b of buf) c = table[(c ^ b) & 0xff]! ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  };
  const chunk = (type: string, data: Buffer): Buffer => {
    const head = Buffer.alloc(4);
    head.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const tail = Buffer.alloc(4);
    tail.writeUInt32BE(crc(body));
    return Buffer.concat([head, body, tail]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // глубина
  ihdr[9] = 2; // RGB
  const rows: Buffer[] = [];
  for (let y = 0; y < size; y++) {
    const row = Buffer.alloc(1 + size * 3); // фильтр 0 + пиксели
    for (let x = 0; x < size; x++) {
      row[1 + x * 3] = (x * 4 + seed * 37) & 0xff;
      row[2 + x * 3] = (y * 4 + seed * 91) & 0xff;
      row[3 + x * 3] = ((x + y) * 2 + seed * 13) & 0xff;
    }
    rows.push(row);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", zlib.deflateSync(Buffer.concat(rows))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}
