/**
 * Обработчики мока онлайна (этап 4a): match `/api/match/v1/...`, connection-токен identity,
 * `GET /api/identity/v1/me`, экраны bff `/pages/online` и `/pages/match/{id}`, лента канала
 * для подменённого транспорта (`/api/match/__e2e/rooms/{code}/feed`).
 *
 * Гость и аноним получают 401/403 на всё онлайн (D13); команды — только с CSRF и `op_id`,
 * повтор `op_id` возвращает сохранённый ответ.
 */
import { http, HttpResponse, type HttpHandler } from "msw";
import { csrfOk, problem, subjectOf } from "./handlers";
import { ONLINE_MODES, type MatchWorld } from "./match";
import type { MockWorld } from "./world";

const NO_STORE = { "Cache-Control": "private, no-store" };

type Ctx = { request: Request; params: Record<string, string | readonly string[] | undefined> };

export function createMatchHandlers(world: MockWorld, mw: MatchWorld): HttpHandler[] {
  const m = "*/api/match/v1";

  /** Пользователь (не гость): id или ответ-ошибка. */
  const userOf = (request: Request): string | Response => {
    const subject = subjectOf(request);
    if (!subject) return problem(401, "auth.unauthenticated");
    if (subject.startsWith("guest.")) return problem(403, "auth.guest_forbidden");
    return subject.slice("user.".length);
  };

  const roomOf = (code: string) => mw.rooms.get(code.toUpperCase()) ?? null;

  /** Обвязка команды комнаты: пользователь, CSRF, участник, op_id, 202 по сценарию. */
  const command =
    (
      run: (args: {
        userId: string;
        room: NonNullable<ReturnType<typeof roomOf>>;
        body: Record<string, unknown>;
      }) => Response | Record<string, unknown>,
      opts: { memberOnly?: boolean } = {},
    ) =>
    async ({ request, params }: Ctx) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      const room = roomOf(String(params.code));
      if (!room || room.phase === "closed") return problem(404, "match.room_not_found");
      const body = (await request.json()) as Record<string, unknown>;
      if (typeof body.op_id !== "string" || !/^[A-Za-z0-9_.:-]{8,128}$/.test(body.op_id))
        return problem(422, "common.validation_failed");
      const key = `${userId}:${body.op_id}`;
      const cached = mw.ops.get(key);
      if (cached) return HttpResponse.json(cached.body as object, { status: cached.status });
      if (opts.memberOnly !== false && !mw.member(room, userId))
        return problem(403, "match.not_participant");
      const out = run({ userId, room, body });
      if (out instanceof Response) return out;
      mw.ops.set(key, { status: 200, body: out });
      if (mw.scenario.acceptOnce) {
        mw.scenario.acceptOnce = false;
        return HttpResponse.json({ accepted: true, version: room.version - 1 }, { status: 202 });
      }
      return HttpResponse.json(out, { headers: NO_STORE });
    };

  const roomResult = (room: NonNullable<ReturnType<typeof roomOf>>) => ({
    version: room.version,
    room: mw.roomView(room),
  });

  const lobbyOnly = (room: NonNullable<ReturnType<typeof roomOf>>) =>
    room.phase === "lobby" ? null : problem(409, "match.phase_mismatch");
  const hostOnly = (room: NonNullable<ReturnType<typeof roomOf>>, userId: string) =>
    room.hostId === userId ? null : problem(403, "match.not_host");

  return [
    // ---------------------------------------------------------------- identity
    http.get("*/api/identity/v1/me", ({ request }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      const u = world.users.find((x) => x.id === userId)!;
      return HttpResponse.json(
        {
          id: u.id,
          nickname: u.nickname,
          display_name: u.nickname,
          email: u.email,
          email_verified: u.verified,
          locale: "ru",
          roles: ["player"],
          perms: [],
          consents: [],
          linked_accounts: [],
        },
        { headers: NO_STORE },
      );
    }),

    http.post("*/api/identity/v1/realtime/token", ({ request }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      return HttpResponse.json(
        { token: `conn.${userId}`, expires_at: new Date(mw.now() + 600_000).toISOString() },
        { headers: { "Cache-Control": "no-store" } },
      );
    }),

    // ---------------------------------------------------------------- лента канала (e2e)
    http.get("*/api/match/__e2e/rooms/:code/feed", ({ request, params }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      if (mw.scenario.feedDown) return new HttpResponse(null, { status: 503 });
      const room = roomOf(String(params.code));
      if (!room || !mw.member(room, userId)) return problem(403, "match.not_participant");
      const after = Number(new URL(request.url).searchParams.get("after") ?? 0);
      return HttpResponse.json(mw.feed(room, after), { headers: NO_STORE });
    }),

    // ---------------------------------------------------------------- match: чтение
    http.get(`${m}/rooms/open`, ({ request }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      return HttpResponse.json({ items: mw.openRooms(), next_cursor: null }, { headers: NO_STORE });
    }),

    http.get(`${m}/rooms/:code`, ({ request, params }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      const room = roomOf(String(params.code));
      if (!room || room.phase === "closed") return problem(404, "match.room_not_found");
      return HttpResponse.json(mw.roomView(room), { headers: NO_STORE });
    }),

    http.get(`${m}/rooms/:code/state`, ({ request, params }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      const room = roomOf(String(params.code));
      if (!room) return problem(404, "match.room_not_found");
      if (!mw.member(room, userId)) return problem(403, "match.not_participant");
      return HttpResponse.json(mw.state(room, userId), { headers: NO_STORE });
    }),

    http.post(`${m}/rooms/:code/tick`, async ({ request, params }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      const room = roomOf(String(params.code));
      if (!room) return problem(404, "match.room_not_found");
      const p = mw.member(room, userId);
      if (!p) return problem(403, "match.not_participant");
      const { known_version } = (await request.json()) as { known_version?: number | null };
      p.lastSeen = mw.now();
      if (p.presence === "disconnected") {
        p.presence = "online";
        mw.publish(room, "player", mw.playerDelta(room, userId));
      }
      const fresh =
        known_version === null || known_version === undefined || room.version > known_version;
      return HttpResponse.json(
        {
          server_now: new Date(mw.now()).toISOString(),
          version: room.version,
          state: fresh ? mw.state(room, userId) : null,
        },
        { headers: NO_STORE },
      );
    }),

    http.post(`${m}/rooms/:code/realtime-token`, ({ request, params }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      const room = roomOf(String(params.code));
      if (!room) return problem(404, "match.room_not_found");
      if (!mw.member(room, userId)) return problem(403, "match.not_participant");
      return HttpResponse.json({
        channel: `room:${room.code}`,
        token: `sub.${room.code}.${userId}`,
        expires_at: new Date(mw.now() + 600_000).toISOString(),
      });
    }),

    http.get(`${m}/matches/:id`, ({ request, params }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      const r = mw.results.get(String(params.id));
      return r ? HttpResponse.json(r) : problem(404, "match.match_not_found");
    }),

    http.get(`${m}/me/matches`, ({ request }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      return HttpResponse.json(
        { items: mw.myMatches(userId), next_cursor: null },
        { headers: NO_STORE },
      );
    }),

    http.post(`${m}/matches/:id/rate`, async ({ request, params }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      const r = mw.results.get(String(params.id));
      if (!r) return problem(404, "match.match_not_found");
      if (!r.players.some((p) => p.user_id === userId))
        return problem(403, "match.not_participant");
      const body = (await request.json()) as { to_user_id: string; kind: string };
      if (body.to_user_id === userId) return problem(422, "match.cannot_rate_self");
      const key = `${r.match_id}:${userId}:${body.to_user_id}:${body.kind}`;
      if (mw.ratings.has(key)) return problem(409, "match.already_rated");
      mw.ratings.add(key);
      return new HttpResponse(null, { status: 204 });
    }),

    // ---------------------------------------------------------------- match: комнаты
    http.post(`${m}/rooms`, async ({ request }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      if (!request.headers.get("idempotency-key")) return problem(422, "idempotency.key_required");
      const body = (await request.json()) as {
        mode_key: string;
        settings?: Record<string, unknown>;
      };
      const def = mw.mode(body.mode_key);
      if (!def) return problem(404, "match.mode_not_found");
      const active = mw.playerRoom.get(userId);
      if (active && mw.rooms.get(active)?.phase !== "closed")
        return problem(409, "match.active_room_conflict", { code: active });
      const room = mw.create(userId, def, (body.settings ?? null) as never);
      return HttpResponse.json(mw.roomView(room), { status: 201, headers: NO_STORE });
    }),

    http.post(
      `${m}/rooms/:code/join`,
      command(
        ({ userId, room }) => {
          if (room.kicked.has(userId)) return problem(403, "match.kicked");
          if (mw.member(room, userId)) return roomResult(room);
          if (room.phase !== "lobby") return problem(409, "match.match_in_progress");
          if (room.players.length >= room.settings.players_max)
            return problem(409, "match.room_full");
          const active = mw.playerRoom.get(userId);
          if (active && active !== room.code && mw.rooms.get(active)?.phase !== "closed")
            return problem(409, "match.active_room_conflict", { code: active });
          mw.addPlayer(room, userId);
          mw.publishLobby(room);
          return roomResult(room);
        },
        { memberOnly: false },
      ),
    ),

    http.post(
      `${m}/rooms/:code/leave`,
      command(({ userId, room }) => {
        if (room.phase === "lobby") {
          room.players = room.players.filter((p) => p.user_id !== userId);
          if (room.hostId === userId && room.players[0]) room.hostId = room.players[0].user_id;
          mw.playerRoom.delete(userId);
          if (!room.players.length) room.phase = "closed";
          else mw.publishLobby(room);
        } else {
          const p = mw.member(room, userId)!;
          p.presence = "left";
          mw.playerRoom.delete(userId);
          mw.publish(room, "player", mw.playerDelta(room, userId));
        }
        return roomResult(room);
      }),
    ),

    http.post(
      `${m}/rooms/:code/ready`,
      command(({ userId, room, body }) => {
        const bad = lobbyOnly(room);
        if (bad) return bad;
        mw.member(room, userId)!.ready = Boolean(body.ready);
        mw.publish(room, "player", mw.playerDelta(room, userId));
        return roomResult(room);
      }),
    ),

    http.post(
      `${m}/rooms/:code/settings`,
      command(({ userId, room, body }) => {
        const bad = lobbyOnly(room) ?? hostOnly(room, userId);
        if (bad) return bad;
        const s = (body.settings ?? {}) as Record<string, unknown>;
        const options = (room.def.options ?? {}) as Record<string, unknown[] | null>;
        for (const [field, value] of Object.entries(s)) {
          if (value === null || value === undefined) continue;
          if (!options[field]?.includes(value))
            return problem(422, "match.setting_not_allowed", { field });
        }
        if (typeof s.players_max === "number" && s.players_max < room.players.length)
          return problem(409, "match.players_over_limit");
        Object.assign(
          room.settings,
          Object.fromEntries(Object.entries(s).filter(([, v]) => v != null)),
        );
        for (const p of room.players) p.ready = false;
        mw.publishLobby(room);
        return roomResult(room);
      }),
    ),

    http.post(
      `${m}/rooms/:code/kick`,
      command(({ userId, room, body }) => {
        const bad = lobbyOnly(room) ?? hostOnly(room, userId);
        if (bad) return bad;
        if (body.user_id === userId) return problem(422, "match.cannot_kick_self");
        if (!mw.member(room, String(body.user_id))) return problem(404, "match.player_not_found");
        room.players = room.players.filter((p) => p.user_id !== body.user_id);
        room.kicked.add(String(body.user_id));
        mw.playerRoom.delete(String(body.user_id));
        mw.publishLobby(room);
        return roomResult(room);
      }),
    ),

    http.post(
      `${m}/rooms/:code/transfer-host`,
      command(({ userId, room, body }) => {
        const bad = hostOnly(room, userId);
        if (bad) return bad;
        if (!mw.member(room, String(body.user_id))) return problem(404, "match.player_not_found");
        room.hostId = String(body.user_id);
        mw.publishLobby(room);
        return roomResult(room);
      }),
    ),

    http.post(
      `${m}/rooms/:code/reset-memory`,
      command(({ userId, room }) => {
        const bad = lobbyOnly(room) ?? hostOnly(room, userId);
        if (bad) return bad;
        room.memory.clear();
        mw.publishLobby(room);
        return roomResult(room);
      }),
    ),

    http.post(
      `${m}/rooms/:code/start`,
      command(({ userId, room, body }) => {
        const bad = lobbyOnly(room) ?? hostOnly(room, userId);
        if (bad) return bad;
        if (body.expected_version !== room.version) return problem(409, "match.phase_mismatch");
        const online = room.players.filter((p) => p.presence === "online");
        if (online.length < room.def.min) return problem(409, "match.not_enough_players");
        if (!online.every((p) => p.ready)) return problem(409, "match.not_all_ready");
        mw.start(room);
        return roomResult(room);
      }),
    ),

    http.post(
      `${m}/rooms/:code/rematch`,
      command(({ userId, room }) => {
        if (room.phase !== "summary") return problem(409, "match.phase_mismatch");
        room.rematch.add(userId);
        const stay = room.players.filter((p) => p.presence !== "left");
        let rematchCode: string | null = null;
        if (stay.every((p) => room.rematch.has(p.user_id))) {
          room.phase = "closed";
          for (const p of stay) mw.playerRoom.delete(p.user_id);
          const next = mw.create(room.hostId, room.def, room.settings);
          for (const p of stay) if (p.user_id !== room.hostId) mw.addPlayer(next, p.user_id);
          rematchCode = next.code;
          mw.publish(room, "closed", { reason: "rematch" });
        }
        return { version: room.version, room: mw.roomView(room), rematch_code: rematchCode };
      }),
    ),

    // ---------------------------------------------------------------- match: ходы
    http.post(
      `${m}/rooms/:code/match/guess`,
      command(({ userId, room, body }) => {
        if (!room.match) return problem(409, "match.phase_mismatch");
        if (body.round_index !== room.match.roundIndex || body.level !== room.match.level)
          return problem(409, "match.phase_mismatch");
        const out = mw.guess(room, userId, body.answer as Record<string, unknown>);
        if ("error" in out) return problem(409, out.error);
        return out;
      }),
    ),

    http.post(
      `${m}/rooms/:code/match/skip`,
      command(({ userId, room }) =>
        mw.skip(room, userId) ? { version: room.version } : problem(409, "match.phase_mismatch"),
      ),
    ),

    http.post(
      `${m}/rooms/:code/match/forfeit`,
      command(({ userId, room }) => {
        if (!room.match || room.phase === "summary") return problem(409, "match.phase_mismatch");
        mw.forfeit(room, userId);
        return { version: room.version };
      }),
    ),

    http.post(
      `${m}/rooms/:code/match/emote`,
      command(({ userId, room, body }) => {
        const list = body.kind === "phrase" ? room.def.chat.phrases : room.def.chat.emotes;
        if (!list.includes(String(body.key))) return problem(422, "match.emote_unknown");
        mw.publish(room, "emote", {
          user_id: userId,
          at: new Date(mw.now()).toISOString(),
          emote: body.kind === "emote" ? body.key : null,
          phrase: body.kind === "phrase" ? body.key : null,
        });
        return { version: room.version };
      }),
    ),

    http.post(
      `${m}/rooms/:code/match/chat`,
      command(({ userId, room, body }) => {
        if (!room.def.chat.free_text) return problem(403, "match.chat_disabled");
        const text = String(body.text ?? "");
        if (text.length > 200) return problem(422, "match.chat_rejected", { reason: "too_long" });
        if (/https?:\/\//i.test(text))
          return problem(422, "match.chat_rejected", { reason: "link" });
        const deny = mw.scenario.chatDeny ?? ["запретка"];
        if (deny.some((w) => text.toLowerCase().includes(w)))
          return problem(422, "match.chat_rejected", { reason: "denylist" });
        mw.publish(room, "chat", {
          message_id: crypto.randomUUID(),
          user_id: userId,
          text,
          at: new Date(mw.now()).toISOString(),
        });
        return { version: room.version };
      }),
    ),

    http.post(
      `${m}/rooms/:code/match/away`,
      command(({ userId, room, body }) => {
        mw.member(room, userId)!.away = Boolean(body.away);
        mw.publish(room, "player", mw.playerDelta(room, userId));
        return { version: room.version };
      }),
    ),

    // ---------------------------------------------------------------- bff
    http.get("*/api/web/v1/pages/online", ({ request }) => {
      const subject = subjectOf(request);
      if (!subject || subject.startsWith("guest."))
        return HttpResponse.json({ status: "auth_required", blocks: null }, { headers: NO_STORE });
      const userId = subject.slice("user.".length);
      return HttpResponse.json(
        {
          status: "ok",
          blocks: {
            open_rooms: { status: "ok", data: { items: mw.openRooms(), next_cursor: null } },
            my_matches: { status: "ok", data: { items: mw.myMatches(userId), next_cursor: null } },
            modes: {
              status: "ok",
              data: ONLINE_MODES.map((d) => ({
                key: d.ref.key,
                version: d.ref.version,
                domain: d.ref.domain,
                title_key: d.ref.title_key,
                source_type: "random",
                clue_type: d.ref.clue_type,
                answer_format: d.ref.answer_format,
                presentation: "score",
                online: {
                  kind: d.ref.kind,
                  players_min: d.min,
                  players_max: d.max,
                  rounds: d.rounds,
                  level_windows_s: Array.from({ length: d.levels }, () => d.windowS),
                  contest_type: d.race ? "race" : "parallel",
                  ranked: false,
                  ladder: null,
                  max_collections: null,
                  lobby: d.options ? { overridable: d.options } : null,
                  chat: d.chat,
                },
              })),
            },
          },
        },
        { headers: NO_STORE },
      );
    }),

    http.get("*/api/web/v1/pages/match/:id", ({ request, params }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      const r = mw.results.get(String(params.id));
      if (!r) return problem(404, "match.match_not_found");
      return HttpResponse.json(
        {
          blocks: {
            match: { status: "ok", data: r },
            items: { status: "ok", data: r.rounds.map((x) => mw.itemCard(x.media_item_id)) },
            library: { status: "ok", data: [] },
          },
        },
        { headers: NO_STORE },
      );
    }),
  ];
}
