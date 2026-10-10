/**
 * Обработчики MSW мок-бэкенда. Пути — как у gateway (`/api/<service>/v1/...`); SSR-запросы
 * к bff по внутреннему адресу (`/v1/...`) сервер e2e/mocks/server.ts переписывает в `/api/web`.
 */
import { http, HttpResponse, type HttpHandler } from "msw";
import type { MockWorld } from "./world";
import { MODES, itemById } from "./world";
import { audioWav, frameSvg } from "./media";

const CSRF = "gv_csrf";
const ACCESS = "gv_access";

function cookiesOf(request: Request): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of (request.headers.get("cookie") ?? "").split(";")) {
    const eq = part.indexOf("=");
    if (eq > 0) out[part.slice(0, eq).trim()] = decodeURIComponent(part.slice(eq + 1).trim());
  }
  return out;
}

/** Субъект по cookie `gv_access` (`guest.<id>` или `user.<id>`) или Bearer. */
function subjectOf(request: Request): string | null {
  const bearer = request.headers.get("authorization")?.replace(/^Bearer\s+/i, "");
  const token = bearer || cookiesOf(request)[ACCESS];
  return token && /^(guest|user)\.[\w-]+$/.test(token) ? token : null;
}

export function problem(status: number, code: string, details?: Record<string, unknown>) {
  return HttpResponse.json(
    { type: `urn:guessverse:problem:${code}`, title: code, status, code, details: details ?? null },
    { status, headers: { "Content-Type": "application/problem+json" } },
  );
}

const csrfOk = (request: Request) => {
  const cookie = cookiesOf(request)[CSRF];
  return Boolean(cookie) && request.headers.get("x-csrf-token") === cookie;
};

const sessionCookies = (access: string) => {
  const csrf = Math.random().toString(36).slice(2);
  return [
    `${ACCESS}=${access}; Path=/; HttpOnly; SameSite=Lax`,
    `gv_refresh=r-${access}; Path=/api/identity/v1/auth; HttpOnly; SameSite=Lax`,
    `${CSRF}=${csrf}; Path=/; SameSite=Lax`,
  ];
};

function withCookies(response: Response, cookies: string[]): Response {
  for (const c of cookies) response.headers.append("Set-Cookie", c);
  return response;
}

export function createHandlers(world: MockWorld): HttpHandler[] {
  const g = "*/api/game/v1";

  /** Общая обвязка хода: субъект, CSRF, идемпотентность, версия. */
  const move =
    (
      run: (args: {
        body: Record<string, unknown>;
        sessionId: string;
        index: number | null;
      }) =>
        | Response
        | { session: string; roundIndex: number; result: "exact" | "partial" | "miss" | null },
    ) =>
    async ({
      request,
      params,
    }: {
      request: Request;
      params: Record<string, string | readonly string[] | undefined>;
    }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.not_authenticated");
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      const key = request.headers.get("idempotency-key");
      if (!key) return problem(422, "idempotency.key_required");
      const cached = world.idempotency.get(`${subject}:${key}`);
      if (cached) return HttpResponse.json(cached.body as object, { status: cached.status });
      const sessionId = String(params.session_id);
      const s = world.sessions.get(sessionId);
      if (!s || s.subject !== subject) return problem(404, "game.session_not_found");
      const body = (await request.json()) as Record<string, unknown>;
      const index = params.index !== undefined ? Number(params.index) : Number(body.round_index);
      if (world.scenario.desyncOnce) {
        world.scenario.desyncOnce = false;
        s.version += 1;
        return problem(409, "game.desync", { session: world.snapshot(s) });
      }
      if (body.expected_version !== s.version)
        return problem(409, "game.desync", { session: world.snapshot(s) });
      const out = run({ body, sessionId, index });
      if (out instanceof Response) return out;
      const r = s.rounds.find((x) => x.index === out.roundIndex)!;
      const payload = {
        result: out.result,
        round: world.roundOut(s, r),
        session: world.snapshot(s),
      };
      world.idempotency.set(`${subject}:${key}`, { status: 200, body: payload });
      return HttpResponse.json(payload, { headers: { "Cache-Control": "no-store" } });
    };

  const activeRound = (sessionId: string, index: number | null) => {
    const s = world.sessions.get(sessionId)!;
    const r = s.rounds.find((x) => x.index === index);
    if (!r || r.status !== "active") return { s, r: null };
    return { s, r };
  };

  return [
    // ---------------------------------------------------------------- game
    http.get(`${g}/modes`, ({ request }) => {
      const domain = new URL(request.url).searchParams.get("domain");
      return HttpResponse.json(MODES.filter((m) => !domain || m.domain === domain));
    }),

    http.get(`${g}/daily`, ({ request }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.not_authenticated");
      const domain = new URL(request.url).searchParams.get("domain");
      return HttpResponse.json(
        MODES.filter((m) => m.source_type === "daily" && (!domain || m.domain === domain)).map(
          (m) => world.dailyStatus(subject, m),
        ),
      );
    }),

    http.post(`${g}/sessions`, async ({ request }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.not_authenticated");
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      if (!request.headers.get("idempotency-key")) return problem(422, "idempotency.key_required");
      const body = (await request.json()) as { mode_key: string; preset_id?: string | null };
      const mode = world.mode(body.mode_key);
      if (!mode) return problem(404, "game.mode_not_found");
      if (!mode.allow_guest && subject.startsWith("guest."))
        return problem(403, "game.guest_not_allowed");
      if (mode.source_type === "daily") {
        if (world.scenario.dailyNotReady) return problem(503, "game.daily_not_ready");
        const existing = world.dailyFor(subject, mode.key);
        if (existing?.status === "finished")
          return problem(409, "game.daily_already_played", { session_id: existing.id });
        if (existing) return HttpResponse.json(world.snapshot(existing), { status: 200 });
      }
      const s = world.startSession(subject, mode, body.preset_id ?? null);
      return HttpResponse.json(world.snapshot(s), { status: 201 });
    }),

    http.get(`${g}/sessions/:session_id`, ({ request, params }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.not_authenticated");
      const s = world.sessions.get(String(params.session_id));
      if (!s || s.subject !== subject) return problem(404, "game.session_not_found");
      return HttpResponse.json(world.snapshot(s), { headers: { "Cache-Control": "no-store" } });
    }),

    http.get(`${g}/sessions/:session_id/result`, ({ request, params }) => {
      const subject = subjectOf(request);
      const s = world.sessions.get(String(params.session_id));
      if (!subject || !s || s.subject !== subject) return problem(404, "game.session_not_found");
      if (s.status !== "finished") return problem(409, "game.session_not_finished");
      return HttpResponse.json(world.result(s));
    }),

    http.post(`${g}/sessions/:session_id/rounds/:index/clue`, async ({ request, params }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.not_authenticated");
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      const s = world.sessions.get(String(params.session_id));
      if (!s || s.subject !== subject) return problem(404, "game.session_not_found");
      const { level } = (await request.json()) as { level: number };
      const r = s.rounds.find((x) => x.index === Number(params.index));
      if (!r || r.status !== "active") return problem(409, "game.round_finished");
      if (level > r.level) return problem(403, "game.level_locked");
      if (world.scenario.assetGoneOnce) {
        world.scenario.assetGoneOnce = false;
        world.voidRound(s, r);
        return problem(410, "game.asset_unavailable");
      }
      const hint = s.mode.levels[level - 1]?.hint ?? {};
      const kind = s.mode.domain === "music" ? ("audio" as const) : ("image" as const);
      const seconds = Number(hint.seconds ?? 0);
      const blur = Number(hint.blur_px ?? 0);
      return HttpResponse.json(
        {
          level,
          kind,
          url: world.assetUrl(kind, seconds, blur, r.index + 1),
          expires_at: new Date(world.now().getTime() + 60_000).toISOString(),
          mime: kind === "audio" ? "audio/wav" : "image/svg+xml",
          // Поля meta — hint_fields загадки ядра (как отдаёт game).
          meta:
            kind === "audio"
              ? { seconds }
              : { blur_px: blur, crop: hint.crop == null ? null : Number(hint.crop) },
        },
        { headers: { "Cache-Control": "no-store" } },
      );
    }),

    http.post(
      `${g}/sessions/:session_id/guesses`,
      move(({ body, sessionId, index }) => {
        const { s, r } = activeRound(sessionId, index);
        if (!r) return problem(409, "game.round_finished");
        const answer = body.answer as { media_item_id?: string; text?: string };
        if (answer.media_item_id && !itemById(answer.media_item_id))
          return problem(422, "game.answer_invalid");
        const result = world.guess(s, r, answer);
        return { session: sessionId, roundIndex: r.index, result };
      }),
    ),

    http.post(
      `${g}/sessions/:session_id/skips`,
      move(({ sessionId, index }) => {
        const { s, r } = activeRound(sessionId, index);
        if (!r) return problem(409, "game.round_finished");
        world.skip(s, r);
        return { session: sessionId, roundIndex: r.index, result: null };
      }),
    ),

    http.post(
      `${g}/sessions/:session_id/rounds/:index/give-up`,
      move(({ sessionId, index }) => {
        const { s, r } = activeRound(sessionId, index);
        if (!r) return problem(409, "game.round_finished");
        world.giveUp(s, r);
        return { session: sessionId, roundIndex: r.index, result: null };
      }),
    ),

    // ---------------------------------------------------------------- media (ассеты по ссылке)
    http.get("*/media/a/:file", ({ request }) => {
      const url = new URL(request.url);
      const asset = world.assets.get(url.pathname);
      if (!asset) return new HttpResponse(null, { status: 403 });
      if (asset.kind === "audio")
        return new HttpResponse(audioWav(asset.seconds, asset.seed), {
          headers: { "Content-Type": "audio/wav" },
        });
      return new HttpResponse(frameSvg(asset.blur, asset.seed), {
        headers: { "Content-Type": "image/svg+xml" },
      });
    }),

    // ---------------------------------------------------------------- catalog
    http.get("*/api/catalog/v1/autocomplete", ({ request }) => {
      const p = new URL(request.url).searchParams;
      const q = p.get("q") ?? "";
      if (q.length > 256) return problem(422, "common.validation_failed");
      return HttpResponse.json({
        items: world.autocomplete(q, p.get("domain"), Number(p.get("limit") ?? 10)),
      });
    }),

    // ---------------------------------------------------------------- bff-web
    http.get("*/api/web/v1/session", ({ request }) => {
      const subject = subjectOf(request);
      if (!subject) return HttpResponse.json({ subject: null });
      if (subject.startsWith("guest."))
        return HttpResponse.json({ subject: { kind: "guest", display_name: null } });
      const user = world.users.find((u) => `user.${u.id}` === subject);
      return HttpResponse.json({ subject: { kind: "user", display_name: user?.nickname ?? null } });
    }),

    http.get("*/api/web/v1/pages/home", ({ request }) => {
      const subject = subjectOf(request);
      return HttpResponse.json({
        blocks: {
          dailies: world.scenario.dailiesUnavailable
            ? { status: "unavailable", data: null }
            : { status: "ok", data: world.dailyCards(subject) },
          modes: { status: "ok", data: world.modeCards() },
        },
      });
    }),

    http.get("*/api/web/v1/pages/solo", ({ request }) => {
      const subject = subjectOf(request);
      const domain = new URL(request.url).searchParams.get("domain");
      return HttpResponse.json({
        blocks: {
          daily: {
            status: "ok",
            data: world.dailyCards(subject).filter((d) => !domain || d.domain === domain),
          },
          random: {
            status: "ok",
            data: world.modeCards(
              (m) => m.source_type === "random" && (!domain || m.domain === domain),
            ),
          },
        },
      });
    }),

    http.get("*/api/web/v1/pages/results/:session_id", ({ request, params }) => {
      const subject = subjectOf(request);
      const s = world.sessions.get(String(params.session_id));
      if (!subject || !s || s.subject !== subject) return problem(404, "game.session_not_found");
      if (s.status !== "finished") return problem(409, "game.session_not_finished");
      return HttpResponse.json(world.resultsPage(s), {
        headers: { "Cache-Control": "private, no-store" },
      });
    }),

    // ---------------------------------------------------------------- identity
    http.post("*/api/identity/v1/auth/guest", ({ request }) => {
      if (subjectOf(request)) return problem(409, "auth.already_authenticated");
      const id = world.nextId(9);
      return withCookies(new HttpResponse(null, { status: 204 }), sessionCookies(`guest.${id}`));
    }),

    http.post("*/api/identity/v1/auth/refresh", () => problem(401, "auth.refresh_invalid")),

    http.post("*/api/identity/v1/auth/login", async ({ request }) => {
      const body = (await request.json()) as { login: string; password: string };
      const login = body.login.trim().toLowerCase();
      const user = world.users.find(
        (u) =>
          (u.email === login || u.nickname.toLowerCase() === login) && u.password === body.password,
      );
      if (!user) return problem(401, "auth.invalid_credentials");
      if (!user.verified) return problem(403, "auth.email_not_verified");
      return withCookies(
        HttpResponse.json({
          expires_in: 900,
          user: { id: user.id, display_name: user.nickname, roles: ["player"], perms: [] },
        }),
        sessionCookies(`user.${user.id}`),
      );
    }),

    http.post("*/api/identity/v1/auth/register", async ({ request }) => {
      const body = (await request.json()) as {
        nickname: string;
        email: string;
        password: string;
        consents?: { kind: string; version: string }[];
      };
      const kinds = new Set((body.consents ?? []).map((c) => c.kind));
      if (!["terms", "privacy", "age_18"].every((k) => kinds.has(k)))
        return problem(422, "auth.consents_required");
      if (body.password.length < 10)
        return problem(422, "auth.password_weak", { reason: "too_short" });
      // Занятый ник или e-mail — тот же 202 (identity не раскрывает существование аккаунта).
      if (
        world.users.some(
          (u) => u.email === body.email.toLowerCase() || u.nickname === body.nickname,
        )
      )
        return HttpResponse.json({ status: "accepted" }, { status: 202 });
      world.users.push({
        id: world.nextId(8),
        nickname: body.nickname,
        email: body.email.toLowerCase(),
        password: body.password,
        verified: false,
      });
      return HttpResponse.json({ status: "accepted" }, { status: 202 });
    }),

    http.post("*/api/identity/v1/auth/verify-email", async ({ request }) => {
      const { token } = (await request.json()) as { token: string };
      if (token !== "valid-token") return problem(422, "auth.email_token_invalid");
      const pending = world.users.find((u) => !u.verified);
      if (pending) pending.verified = true;
      return new HttpResponse(null, { status: 204 });
    }),

    http.post("*/api/identity/v1/auth/logout", () =>
      withCookies(new HttpResponse(null, { status: 204 }), [
        `${ACCESS}=; Path=/; Max-Age=0`,
        `${CSRF}=; Path=/; Max-Age=0`,
      ]),
    ),
  ];
}
