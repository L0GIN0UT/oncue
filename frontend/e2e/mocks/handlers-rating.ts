/**
 * Мок этапа 4b по контрактам `contracts/openapi/{match,rating,bff_web}.json`: очередь рейтинговых
 * дуэлей match (`/queue/*`), изменения рейтинга rating (`/matches/{id}/changes`), блоки 4b хаба
 * `/pages/online` (см. `ratingBlocks`) и страница `/pages/leaderboards`.
 *
 * Подбор — двусторонний: второй игрок в той же очереди (ladder + формат) создаёт комнату с
 * первым; первый узнаёт о матче на ближайшем `tick`.
 */
import { http, HttpResponse, type HttpHandler } from "msw";
import { csrfOk, problem, subjectOf } from "./handlers";
import type { MatchWorld } from "./match";
import { ONLINE_MODES, QUEUE_TICK_S } from "./match";
import type { MockWorld } from "./world";

const NO_STORE = { "Cache-Control": "private, no-store" };
const LADDERS = [
  { key: "film", domain: "film" as const, formats: ["classic", "lite"] as const },
  { key: "music", domain: "music" as const, formats: ["classic", "lite"] as const },
];
const DAY_MS = 86_400_000;
const PAGE_SIZE = 10;
const BOARD_SIZE = 25;
/** Моё место в мок-таблице при `around=me`. */
const MY_PLACE = 18;

const tier = (family: string, step: number | null, titleKey = `rating.tier.${family}`) => ({
  family,
  key: step ? `${family}_${step}` : family,
  step,
  title_key: titleKey,
});

type Format = "classic" | "lite";

/** Блоки 4b хаба онлайна для пользователя `userId`. */
export function ratingBlocks(mw: MatchWorld) {
  const now = mw.now();
  const waiting = (ladder: string, format: Format) =>
    [...mw.queue.values()].filter((q) => q.ladder === ladder && q.format === format).length;
  return {
    queues: {
      status: "ok",
      data: LADDERS.flatMap((l) =>
        l.formats.map((format) => ({
          ladder: l.key,
          format,
          searching: waiting(l.key, format),
        })),
      ),
    },
    ratings: {
      status: "ok",
      data: [
        {
          ladder: "film",
          rating: null,
          tier: null,
          next_tier_at: null,
          calibrating: true,
          calibration_left: 7,
          matches_played: 3,
          place: null,
        },
        {
          ladder: "music",
          rating: 1120,
          tier: tier("bronze", 2),
          next_tier_at: 1200,
          calibrating: false,
          calibration_left: 0,
          matches_played: 14,
          place: MY_PLACE,
        },
      ],
    },
    seasons: {
      status: "ok",
      data: LADDERS.map((l) => ({
        ladder: l.key,
        number: 3,
        starts_at: new Date(now - 44 * DAY_MS).toISOString(),
        ends_at: new Date(now + 12 * DAY_MS).toISOString(),
      })),
    },
    weekly: { status: "ok", data: LADDERS.map((l) => weeklyTop(mw, l.key)) },
  };
}

function weeklyTop(mw: MatchWorld, ladder: string) {
  const monday = new Date(mw.now());
  monday.setUTCDate(monday.getUTCDate() - ((monday.getUTCDay() + 6) % 7));
  return {
    ladder,
    week_start: monday.toISOString().slice(0, 10),
    items: [1, 2, 3].map((place) => ({
      place,
      user_id: boardUserId(place),
      nickname: `игрок${place}`,
      delta: 60 - place * 15,
    })),
    me: null,
  };
}

const boardUserId = (place: number) => `00000000-0000-4000-8000-${String(place).padStart(12, "0")}`;

function boardEntry(place: number) {
  return {
    place,
    user_id: boardUserId(place),
    nickname: `игрок${place}`,
    rating: 1600 - place * 12,
    tier: place <= 3 ? tier("platinum", 3) : place <= 12 ? tier("gold", 2) : tier("silver", 1),
    matches_played: 80 - place,
  };
}

export function createRatingHandlers(world: MockWorld, mw: MatchWorld): HttpHandler[] {
  const m = "*/api/match/v1";

  const userOf = (request: Request): string | Response => {
    const subject = subjectOf(request);
    if (!subject) return problem(401, "auth.unauthenticated");
    if (subject.startsWith("guest.")) return problem(403, "auth.guest_forbidden");
    return subject.slice("user.".length);
  };

  const ticket = (userId: string, slowFromTicks = Infinity) => {
    const matched = mw.queueMatched.get(userId);
    if (matched) {
      return {
        ladder: matched.ladder,
        format: matched.format,
        state: "matched",
        entered_at: new Date(mw.now()).toISOString(),
        wait_s: 0,
        window: 100,
        tick_s: QUEUE_TICK_S,
        slow: false,
        room_code: matched.code,
      };
    }
    const q = mw.queue.get(userId)!;
    return {
      ladder: q.ladder,
      format: q.format,
      state: "searching",
      entered_at: new Date(q.enteredAt).toISOString(),
      wait_s: Math.floor((mw.now() - q.enteredAt) / 1000),
      window: 100,
      tick_s: QUEUE_TICK_S,
      slow: Boolean(mw.scenario.queueSlow) || q.ticks >= slowFromTicks,
      room_code: null,
    };
  };

  return [
    // ---------------------------------------------------------------- match: очередь
    http.post(`${m}/queue/enter`, async ({ request }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      if (!request.headers.get("idempotency-key")) return problem(422, "idempotency.key_required");
      const body = (await request.json()) as { ladder: string; format: Format };
      if (!LADDERS.some((l) => l.key === body.ladder))
        return problem(404, "match.ladder_not_found");
      const def = ONLINE_MODES.find((d) => d.ref.key === `${body.ladder}.ranked.${body.format}`);
      if (!def) return problem(422, "match.queue_format_unavailable");
      // Повтор того же входа — то же состояние.
      if (mw.queue.has(userId)) return HttpResponse.json(ticket(userId), { headers: NO_STORE });
      const active = mw.playerRoom.get(userId);
      if (active && mw.rooms.get(active)?.phase !== "closed")
        return problem(409, "match.active_room_conflict", { code: active });

      const rival = [...mw.queue.entries()].find(
        ([id, q]) => id !== userId && q.ladder === body.ladder && q.format === body.format,
      );
      if (rival) {
        const [rivalId] = rival;
        mw.queue.delete(rivalId);
        const room = mw.create(rivalId, def, null);
        mw.addPlayer(room, userId);
        const found = { ladder: body.ladder, format: body.format, code: room.code };
        mw.queueMatched.set(rivalId, found);
        mw.queueMatched.set(userId, found);
        return HttpResponse.json(ticket(userId), { headers: NO_STORE });
      }
      mw.queue.set(userId, {
        ladder: body.ladder,
        format: body.format,
        enteredAt: mw.now(),
        ticks: 0,
      });
      return HttpResponse.json(ticket(userId), { headers: NO_STORE });
    }),

    http.post(`${m}/queue/tick`, ({ request }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      const q = mw.queue.get(userId);
      if (q) q.ticks += 1;
      else if (!mw.queueMatched.has(userId)) return problem(404, "match.not_in_queue");
      return HttpResponse.json(ticket(userId, 2), { headers: NO_STORE });
    }),

    http.post(`${m}/queue/leave`, ({ request }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      mw.queue.delete(userId);
      return new HttpResponse(null, { status: 204 });
    }),

    http.get(`${m}/queue/pool`, ({ request }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      return HttpResponse.json(
        {
          items: ratingBlocks(mw).queues.data,
        },
        { headers: NO_STORE },
      );
    }),

    // ---------------------------------------------------------------- rating
    http.get("*/api/rating/v1/matches/:id/changes", ({ request, params }) => {
      const userId = userOf(request);
      if (userId instanceof Response) return userId;
      if (mw.scenario.ratingDown) return problem(503, "rating.unavailable");
      const result = mw.results.get(String(params.id));
      const delay = mw.scenario.ratingDelayMs ?? 1500;
      if (
        !result?.mode.ranked ||
        !result.players.some((p) => p.user_id === userId) ||
        mw.now() < Date.parse(result.finished_at) + delay
      )
        return problem(404, "rating.match_not_rated");
      const ladder = result.mode.domain;
      const winner = result.players.find((p) => p.outcome === "win");
      return HttpResponse.json(
        {
          match_id: result.match_id,
          items: result.players.map((p) => {
            const won = p.user_id === winner?.user_id;
            const base = {
              user_id: p.user_id,
              ladder,
              format: result.mode.answer_format === "pick" ? "lite" : "classic",
              outcome: p.outcome ?? "draw",
              leaver_penalty: p.finish === "forfeit" ? 10 : 0,
            };
            // Победитель — на границе звания (растёт), проигравший ещё в калибровке.
            return won
              ? {
                  ...base,
                  old: 1195,
                  new: 1211,
                  delta: 16,
                  old_tier: tier("bronze", 3),
                  tier: tier("silver", 1),
                  calibrating: false,
                  calibration_left: 0,
                }
              : {
                  ...base,
                  old: null,
                  new: null,
                  delta: null,
                  old_tier: null,
                  tier: null,
                  calibrating: true,
                  calibration_left: 9,
                };
          }),
        },
        { headers: NO_STORE },
      );
    }),

    // ---------------------------------------------------------------- bff: лидерборды
    http.get("*/api/web/v1/pages/leaderboards", ({ request }) => {
      const q = new URL(request.url).searchParams;
      const ladder = q.get("ladder") ?? LADDERS[0]!.key;
      const cursor = q.get("cursor");
      const around = q.get("around");
      const subject = subjectOf(request);
      if (around === "me" && (!subject || subject.startsWith("guest.")))
        return problem(401, "auth.unauthenticated");
      if (!LADDERS.some((l) => l.key === ladder)) return problem(404, "bff.not_found");
      if (cursor !== null && !/^o\d+$/.test(cursor)) return problem(422, "bff.cursor_invalid");

      const myId = subject?.startsWith("user.") ? subject.slice(5) : null;
      const mine = myId
        ? {
            ...boardEntry(MY_PLACE),
            user_id: myId,
            nickname: world.users.find((u) => u.id === myId)?.nickname ?? "я",
          }
        : null;
      const offset =
        around === "me" && !cursor ? MY_PLACE - 3 : cursor ? Number(cursor.slice(1)) : 0;
      const size = around === "me" && !cursor ? 5 : PAGE_SIZE;
      const items = Array.from({ length: Math.min(size, BOARD_SIZE - offset) }, (_, i) =>
        boardEntry(offset + i + 1),
      ).map((e) => (mine && e.place === MY_PLACE ? mine : e));
      const next = offset + size < BOARD_SIZE ? `o${offset + size}` : null;
      return HttpResponse.json(
        {
          blocks: {
            ladders: {
              status: "ok",
              data: LADDERS.map((l) => ({
                key: l.key,
                domain: l.domain,
                formats: [...l.formats],
                season: null,
                tiers: [],
              })),
            },
            leaderboard: {
              status: "ok",
              data: { ladder, items, next_cursor: next, me: around === "me" ? mine : null },
            },
            weekly: { status: "ok", data: weeklyTop(mw, ladder) },
          },
        },
        { headers: around === "me" ? NO_STORE : { "Cache-Control": "public, max-age=10" } },
      );
    }),
  ];
}
