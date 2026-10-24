/**
 * Обработчики MSW мок-бэкенда. Пути — как у gateway (`/api/<service>/v1/...`); SSR-запросы
 * к bff по внутреннему адресу (`/v1/...`) сервер e2e/mocks/server.ts переписывает в `/api/web`.
 */
import { http, HttpResponse, type HttpHandler } from "msw";
import type { MockWorld } from "./world";
import { MODES, itemById } from "./world";
import { audioWav, frameSvg } from "./media";
import { createUgcHandlers } from "./handlers-3b";
import { createMatchHandlers } from "./handlers-match";
import { createRatingHandlers } from "./handlers-rating";
import { createProgressionHandlers, homeProgress } from "./progression";
import { createEconomyHandlers } from "./economy";
import { createNotificationsHandlers } from "./notifications";
import { onlineModesOut, type MatchWorld } from "./match";
import { COLLECTION_MODES } from "./ugc";

/** Код подтверждения мок-identity (ADR 0041): в e2e против мока он всегда один. */
export const MOCK_CODE = "123456";
const challengeBody = (id: string, purpose: string) => ({
  challenge_id: id,
  code_length: 6,
  expires_in_s: 600,
  purpose,
  resend_after_s: 0,
});
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
export function subjectOf(request: Request): string | null {
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

/** Субъект живой refresh-сессии из cookie `gv_refresh` (`r-<субъект>`), как у identity. */
function refreshSubjectOf(request: Request, world: MockWorld): string | null {
  const raw = cookiesOf(request).gv_refresh;
  const subject = raw?.startsWith("r-") ? raw.slice(2) : null;
  if (!subject || !/^(guest|user)\.[\w-]+$/.test(subject)) return null;
  return world.revokedRefresh.has(subject) ? null : subject;
}

export const csrfOk = (request: Request) => {
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

export function createHandlers(world: MockWorld, matchWorld?: MatchWorld): HttpHandler[] {
  // challenge_id -> id пользователя, ожидающего подтверждения e-mail (код всегда MOCK_CODE).
  const pendingByChallenge = new Map<string, string>();
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
      if (!subject) return problem(401, "auth.unauthenticated");
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
      // Статистика выбора — только у сетки (ход выбором варианта с единицей).
      const stats = r.picked ? { agree_pct: world.agreePct(r) } : null;
      const payload = {
        result: out.result,
        round: world.roundOut(s, r),
        session: world.snapshot(s),
        stats,
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
      const params = new URL(request.url).searchParams;
      const domain = params.get("domain");
      const kind = params.get("kind") ?? "play";
      if (params.get("session") === "online")
        return HttpResponse.json(
          onlineModesOut().filter((m) => !domain || m.domain === domain),
          { headers: { "Cache-Control": "public, max-age=30" } },
        );
      if (kind !== "play" && kind !== "collection") return problem(422, "common.validation_failed");
      const list = kind === "collection" ? COLLECTION_MODES : MODES;
      return HttpResponse.json(
        list.filter((m) => !domain || m.domain === domain),
        {
          headers: { "Cache-Control": "public, max-age=30" },
        },
      );
    }),

    http.get(`${g}/daily`, ({ request }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.unauthenticated");
      const domain = new URL(request.url).searchParams.get("domain");
      return HttpResponse.json(
        MODES.filter((m) => world.isScheduled(m) && (!domain || m.domain === domain)).map((m) =>
          world.dailyStatus(subject, m),
        ),
      );
    }),

    http.post(`${g}/sessions`, async ({ request }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.unauthenticated");
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      if (!request.headers.get("idempotency-key")) return problem(422, "idempotency.key_required");
      const body = (await request.json()) as {
        mode_key: string;
        preset_id?: string | null;
        subject_ids?: string[] | null;
        collection_id?: string | null;
        rounds?: number | "all" | null;
      };
      const mode = world.mode(body.mode_key);
      if (!mode) return problem(404, "game.mode_not_found");
      // Сборник (этап 3b, D4): режим с source `collection` и опубликованный сборник домена.
      if (mode.source_type === "collection" || body.collection_id) {
        if (mode.source_type !== "collection") return problem(422, "game.collection_not_supported");
        if (!body.collection_id) return problem(422, "game.collection_required");
        const c = world.ugc.find(body.collection_id);
        if (!c || c.status !== "published") return problem(404, "game.collection_not_found");
        if (c.domain !== mode.domain) return problem(422, "game.collection_domain_mismatch");
        const options = [5, 10, 25, "all"] as const;
        const rounds = body.rounds ?? options[0];
        if (!options.includes(rounds as (typeof options)[number]))
          return problem(422, "game.rounds_not_allowed", { requested: rounds, options });
        const pool = world.ugc.playableItems(c, mode);
        const count = rounds === "all" ? pool.length : rounds;
        if (pool.length < 5 || count > pool.length) return problem(422, "game.pool_too_small");
        const s = world.startSession(subject, mode, null, [], {
          id: c.id,
          version: c.version,
          itemIds: pool.slice(0, count),
        });
        return HttpResponse.json(world.snapshot(s), { status: 201 });
      }
      if (!mode.allow_guest && subject.startsWith("guest."))
        return problem(403, "game.guest_not_allowed");
      const subjectIds = body.subject_ids ?? [];
      const pick = mode.start?.subject ?? null;
      if (!pick && subjectIds.length) return problem(422, "game.subjects_not_supported");
      if (pick && !subjectIds.length) return problem(422, "game.subject_required");
      if (pick && subjectIds.length > pick.max) return problem(422, "game.too_many_subjects");
      if (world.isScheduled(mode)) {
        if (world.scenario.dailyNotReady) return problem(503, "game.daily_not_ready");
        const existing = world.dailyFor(subject, mode.key);
        if (existing?.status === "finished")
          return problem(409, "game.daily_already_played", { session_id: existing.id });
        if (existing) return HttpResponse.json(world.snapshot(existing), { status: 200 });
      }
      const s = world.startSession(subject, mode, body.preset_id ?? null, subjectIds);
      if (!s.rounds.length) return problem(503, "game.pool_empty");
      return HttpResponse.json(world.snapshot(s), { status: 201 });
    }),

    http.get(`${g}/modes/:mode_key/subjects`, ({ request, params }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.unauthenticated");
      const mode = world.mode(String(params.mode_key));
      if (!mode) return problem(404, "game.mode_not_found");
      if (!mode.start?.subject) return problem(422, "game.subjects_not_supported");
      const p = new URL(request.url).searchParams;
      const limit = Math.min(20, Math.max(1, Number(p.get("limit") ?? 20)));
      return HttpResponse.json(world.subjects(mode, p.get("q") ?? "", limit));
    }),

    http.post(`${g}/sessions/:session_id/rounds/:index/final-clue`, ({ request, params }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.unauthenticated");
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      const s = world.sessions.get(String(params.session_id));
      if (!s || s.subject !== subject) return problem(404, "game.session_not_found");
      const r = s.rounds.find((x) => x.index === Number(params.index));
      if (!r || r.status !== "finished") {
        if (r?.status === "void") return problem(410, "game.asset_unavailable");
        return problem(409, "game.round_not_finished");
      }
      return HttpResponse.json(world.freshFinalClue(s, r), {
        headers: { "Cache-Control": "no-store" },
      });
    }),

    http.get(`${g}/sessions/:session_id`, ({ request, params }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.unauthenticated");
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
      if (!subject) return problem(401, "auth.unauthenticated");
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
      return HttpResponse.json(world.levelClue(s, r, level), {
        headers: { "Cache-Control": "no-store" },
      });
    }),

    http.post(
      `${g}/sessions/:session_id/guesses`,
      move(({ body, sessionId, index }) => {
        const { s, r } = activeRound(sessionId, index);
        if (!r) return problem(409, "game.round_finished");
        const answer = body.answer as { media_item_id?: string; text?: string; option_id?: string };
        const picks = (s.mode.answer_format ?? "classic") === "pick";
        if (picks !== (answer.option_id !== undefined))
          return problem(422, "game.answer_kind_unsupported");
        if (
          answer.option_id !== undefined &&
          !r.options.some((o) => o.option_id === answer.option_id)
        )
          return problem(422, "game.option_unknown");
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
      const asset = world.asset(url.pathname);
      if (!asset) return new HttpResponse(null, { status: 403 });
      if (asset.kind === "text")
        return HttpResponse.json(asset.body as object, {
          headers: { "Content-Type": "application/json" },
        });
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
          weekly: world.scenario.dailiesUnavailable
            ? { status: "unavailable", data: null }
            : { status: "ok", data: world.dailyCards(subject, true) },
          progress: homeProgress(world, subject),
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
          modes: { status: "ok", data: world.modeGroups(domain) },
          weekly: {
            status: "ok",
            data: world.dailyCards(subject, true).filter((d) => !domain || d.domain === domain),
          },
          records: subject?.startsWith("user.")
            ? { status: "ok", data: world.recordCards(subject) }
            : null,
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

    // ---------------------------------------------------------------- этап 3b
    ...createUgcHandlers(world),
    ...createProgressionHandlers(world),
    ...createEconomyHandlers(world),
    ...createNotificationsHandlers(world),

    // ---------------------------------------------------------------- этап 4a: онлайн
    ...(matchWorld ? createMatchHandlers(world, matchWorld) : []),
    ...(matchWorld ? createRatingHandlers(world, matchWorld) : []),

    // ---------------------------------------------------------------- identity
    http.post("*/api/identity/v1/auth/guest", ({ request }) => {
      // Как identity: 409 и при действующем access, и при живой refresh-сессии.
      if (subjectOf(request) || refreshSubjectOf(request, world))
        return problem(409, "auth.already_authenticated");
      const id = world.nextId(9);
      return withCookies(new HttpResponse(null, { status: 204 }), sessionCookies(`guest.${id}`));
    }),

    http.post("*/api/identity/v1/auth/refresh", ({ request }) => {
      const subject = refreshSubjectOf(request, world);
      if (!subject) return problem(401, "auth.refresh_invalid");
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      world.refreshCalls += 1;
      return withCookies(HttpResponse.json({ expires_in: 900 }), sessionCookies(subject));
    }),

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
          status: "authenticated",
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
      // Занятый ник или e-mail — тот же 202 с таким же challenge (identity не раскрывает аккаунт).
      const taken = world.users.some(
        (u) => u.email === body.email.toLowerCase() || u.nickname === body.nickname,
      );
      const challengeId = crypto.randomUUID();
      if (!taken) {
        const id = world.nextId(8);
        world.users.push({
          id,
          nickname: body.nickname,
          email: body.email.toLowerCase(),
          password: body.password,
          verified: false,
        });
        pendingByChallenge.set(challengeId, id);
      }
      return HttpResponse.json(challengeBody(challengeId, "register"), { status: 202 });
    }),

    http.post("*/api/identity/v1/auth/verify-email", async ({ request }) => {
      const { challenge_id, code } = (await request.json()) as {
        challenge_id: string;
        code: string;
      };
      if (code !== MOCK_CODE) return problem(422, "auth.code_invalid", { attempts_left: 4 });
      const user = world.users.find((u) => u.id === pendingByChallenge.get(challenge_id));
      if (!user) return problem(422, "auth.code_invalid", { attempts_left: 4 });
      user.verified = true;
      pendingByChallenge.delete(challenge_id);
      return withCookies(
        HttpResponse.json({
          expires_in: 900,
          user: { id: user.id, display_name: user.nickname, roles: ["player"], perms: [] },
        }),
        sessionCookies(`user.${user.id}`),
      );
    }),

    http.post("*/api/identity/v1/auth/logout", ({ request }) => {
      const subject = refreshSubjectOf(request, world) ?? subjectOf(request);
      if (subject) world.revokedRefresh.add(subject);
      return withCookies(new HttpResponse(null, { status: 204 }), [
        `${ACCESS}=; Path=/; Max-Age=0`,
        `gv_refresh=; Path=/api/identity/v1/auth; Max-Age=0`,
        `${CSRF}=; Path=/; Max-Age=0`,
      ]);
    }),
  ];
}
