/**
 * Мок social (этап 5c) по `contracts/openapi/social.json`: друзья и заявки, код добавления,
 * блокировки, приватность, присутствие, приглашения, комментарии сборников, жалобы и санкции.
 * Блоки bff (`social` профиля, `friends` хаба, `comments` сборника, `sanctions` shell,
 * `can_report` итога) собираются из того же состояния функциями `*Block` — их подключают
 * мок-обработчики bff. Управление сценариями: `POST /__mock/scenario {social: {...}}`.
 */
import { http, HttpResponse, type HttpHandler } from "msw";
import type {
  BlockView,
  CommentView,
  FriendRequestView,
  FriendView,
  MySanction,
  PrivacySettings,
  ReportReason,
} from "../../src/shared/api/types";
import { csrfOk, problem, subjectOf } from "./handlers";
import type { MockWorld } from "./world";

const NO_STORE = { "Cache-Control": "private, no-store" };

const uid = (n: number) => `00000000-0000-4000-8000-0000000009${String(n).padStart(2, "0")}`;
/** Игроки справочника мока (кроме тестового `…901`, это сам зритель). */
export const SOCIAL_USERS = {
  anna: { id: uid(10), nickname: "Анна" },
  boris: { id: uid(11), nickname: "Борис" },
  clara: { id: uid(12), nickname: "Клара" },
  dmitry: { id: uid(13), nickname: "Дмитрий" },
} as const;

/** Сборник, под которым в моке уже есть 3 комментария (страница 2 + «показать ещё»). */
export const COMMENTED_COLLECTION = "00000000-0000-4000-a000-000000000001";

export type SocialScenario = {
  /** Приглашение отвечает 403 `social.invite_forbidden`. */
  inviteForbidden?: boolean;
  /** Приглашение отвечает 429. */
  inviteRateLimited?: boolean;
  /** Публикация комментария: код ошибки social. */
  commentError?: "blocked" | "chat_muted" | "comment_rejected" | "comments_rate_limit";
  /** social недоступен (503). */
  socialDown?: boolean;
  /** Профиль игрока по id закрыт (`restricted`). */
  restrictedPlayer?: string;
  /** Действующие санкции зрителя. */
  sanctions?: MySanction["kind"][];
  /** `match.online_banned` при создании комнаты. */
  onlineBanned?: boolean;
  /** Вход закрыт `auth.account_banned`. */
  accountBanned?: boolean;
  /**
   * Аккаунт забанили при уже открытой сессии: access истёк (401 `auth.token_expired`),
   * а refresh отвечает 403 `auth.account_banned`.
   */
  sessionBanned?: boolean;
  /** Жалоба отвечает 429. */
  reportRateLimited?: boolean;
};

const REASONS: ReportReason[] = [
  { code: "abuse", title: "Оскорбления", target_kinds: ["player", "comment", "collection"] },
  { code: "spam", title: "Спам", target_kinds: ["comment", "collection"] },
  { code: "cheating", title: "Читерство", target_kinds: ["player"] },
  {
    code: "inappropriate",
    title: "Неподобающее изображение",
    target_kinds: ["avatar", "collection"],
  },
];

const DEFAULT_PRIVACY: PrivacySettings = {
  profile_public: true,
  stats_public: true,
  friend_requests: "all",
};

const DAY = "2026-10-20T10:00:00Z";

export class SocialWorld {
  friends: FriendView[] = [];
  incoming: FriendRequestView[] = [];
  outgoing: FriendRequestView[] = [];
  blocks: BlockView[] = [];
  privacy: PrivacySettings = { ...DEFAULT_PRIVACY };
  code = "ABCD2345";
  comments = new Map<string, CommentView[]>();
  /** Подан репорт: ключ `kind:id` → id жалобы. */
  reports = new Map<string, string>();
  reportBodies: unknown[] = [];
  invites: { to: string; room_code: string; key: string | null }[] = [];
  presenceCalls = 0;
  idempotency = new Map<string, object>();
  private seq = 0;

  constructor() {
    this.reset();
  }

  reset(): void {
    this.seq = 0;
    this.friends = [
      { user_id: SOCIAL_USERS.anna.id, nickname: "Анна", status: "online", friends_since: DAY },
      { user_id: SOCIAL_USERS.boris.id, nickname: "Борис", status: "in_match", friends_since: DAY },
      { user_id: SOCIAL_USERS.clara.id, nickname: "Клара", status: "offline", friends_since: DAY },
    ];
    this.incoming = [
      { id: this.id("req"), user_id: SOCIAL_USERS.dmitry.id, nickname: "Дмитрий", created_at: DAY },
    ];
    this.outgoing = [];
    this.blocks = [];
    this.privacy = { ...DEFAULT_PRIVACY };
    this.code = "ABCD2345";
    this.comments = new Map([[COMMENTED_COLLECTION, makeComments(this, COMMENTED_COLLECTION, 3)]]);
    this.reports = new Map();
    this.reportBodies = [];
    this.invites = [];
    this.presenceCalls = 0;
    this.idempotency = new Map();
  }

  id(prefix: string): string {
    this.seq += 1;
    const n = String(this.seq).padStart(12, "0");
    return `0192b000-0000-7000-8${prefix === "req" ? "001" : prefix === "cmt" ? "002" : "003"}-${n}`;
  }

  isFriend(userId: string): boolean {
    return this.friends.some((f) => f.user_id === userId);
  }

  isBlocked(userId: string): boolean {
    return this.blocks.some((b) => b.user_id === userId);
  }

  nicknameOf(userId: string): string | null {
    return Object.values(SOCIAL_USERS).find((u) => u.id === userId)?.nickname ?? null;
  }

  commentsOf(collectionId: string): CommentView[] {
    return this.comments.get(collectionId) ?? [];
  }
}

function viewerId(request: Request): string | null {
  const s = subjectOf(request);
  return s?.startsWith("user.") ? s.slice(5) : null;
}

function userOnly(request: Request, world: MockWorld): Response | null {
  if (world.scenario.social?.socialDown) return problem(503, "social.unavailable");
  const subject = subjectOf(request);
  if (!subject) return problem(401, "auth.unauthenticated");
  if (!subject.startsWith("user.")) return problem(403, "auth.guest_forbidden");
  return null;
}

const empty = () => new HttpResponse(null, { status: 204 });
const json = (body: object | undefined, status = 200) =>
  HttpResponse.json(body, { status, headers: NO_STORE });

// ------------------------------------------------------------------ блоки bff
/** Блок `social` профиля игрока. */
export function playerSocialBlock(world: MockWorld, playerId: string) {
  const s = world.social;
  return {
    status: "ok" as const,
    data: {
      blocked_by_me: s.isBlocked(playerId),
      can_send_request: !s.isFriend(playerId) && s.privacy.friend_requests === "all",
      is_friend: s.isFriend(playerId),
    },
  };
}

export function isRestricted(world: MockWorld, playerId: string): boolean {
  return world.scenario.social?.restrictedPlayer === playerId || world.social.isBlocked(playerId);
}

/** Блок `friends` хаба онлайна. */
export function friendsBlock(world: MockWorld) {
  return {
    status: "ok" as const,
    data: {
      incoming_requests: world.social.incoming.length,
      items: world.social.friends.map(({ user_id, nickname, status }) => ({
        user_id,
        nickname,
        status,
      })),
    },
  };
}

/** Блок `comments` страницы сборника (первая страница). */
export function commentsBlock(world: MockWorld, collectionId: string, subject: string | null) {
  return { status: "ok" as const, data: commentPage(world, collectionId, null, subject) };
}

/** Блок `sanctions` shell. */
export function sanctionsBlock(world: MockWorld) {
  const kinds = world.scenario.social?.sanctions ?? [];
  return { status: "ok" as const, data: { items: kinds.map(sanctionOf) } };
}

const sanctionOf = (kind: MySanction["kind"]): MySanction => ({
  kind,
  reason: "Нарушение правил общения",
  started_at: "2026-10-23T10:00:00Z",
  expires_at: kind === "account" ? null : "2026-11-23T10:00:00Z",
});

const PAGE = 2;
function commentPage(
  world: MockWorld,
  collectionId: string,
  cursor: string | null,
  subject: string | null,
) {
  const all = [...world.social.commentsOf(collectionId)].sort((a, b) =>
    b.created_at.localeCompare(a.created_at),
  );
  const start = Number(cursor ?? 0);
  const items = all.slice(start, start + PAGE).map((c) => viewOf(world, c, subject));
  return {
    items,
    next_cursor: start + PAGE < all.length ? String(start + PAGE) : null,
    total: all.length,
  };
}

function viewOf(world: MockWorld, c: CommentView, subject: string | null): CommentView {
  const me = subject?.startsWith("user.") ? subject.slice(5) : null;
  const fresh = Date.now() - new Date(c.created_at).getTime() < 5 * 60_000;
  return { ...c, can_edit: me === c.author_id && fresh, can_delete: me === c.author_id };
}

/** Страница комментариев в API: курсор по смещению (в моке достаточно). */
export const COMMENTS_MOCK_PAGE = PAGE;

function makeComments(social: SocialWorld, collectionId: string, count: number): CommentView[] {
  const list: CommentView[] = [];
  for (let i = 0; i < count; i += 1) {
    list.push({
      id: social.id("cmt"),
      collection_id: collectionId,
      author_id: SOCIAL_USERS.anna.id,
      author_nickname: "Анна",
      body: `Комментарий ${i + 1}`,
      created_at: new Date(Date.UTC(2026, 9, 1, 10, i)).toISOString(),
      edited_at: null,
      can_edit: false,
      can_delete: false,
    });
  }
  return list;
}

export function seedComments(world: MockWorld, collectionId: string, count: number): void {
  world.social.comments.set(collectionId, makeComments(world.social, collectionId, count));
}

export function createSocialHandlers(world: MockWorld): HttpHandler[] {
  const s = world.social;
  const base = "*/api/social/v1";

  const guard = (request: Request, mutating = false): Response | null => {
    const err = userOnly(request, world);
    if (err) return err;
    if (mutating && !csrfOk(request)) return problem(403, "auth.csrf_failed");
    return null;
  };

  return [
    // ---------------------------------------------------------------- друзья
    http.get(`${base}/me/friends`, ({ request }) => {
      const err = guard(request);
      if (err) return err;
      return json({ items: s.friends, total: s.friends.length });
    }),

    http.delete(`${base}/me/friends/:id`, ({ request, params }) => {
      const err = guard(request, true);
      if (err) return err;
      s.friends = s.friends.filter((f) => f.user_id !== String(params.id));
      return empty();
    }),

    http.post(`${base}/me/friends/:id/invite`, async ({ request, params }) => {
      const err = guard(request, true);
      if (err) return err;
      const sc = world.scenario.social;
      if (sc?.inviteRateLimited) {
        const res = problem(429, "rate_limited");
        res.headers.set("Retry-After", "20");
        return res;
      }
      if (sc?.inviteForbidden || !s.isFriend(String(params.id)))
        return problem(403, "social.invite_forbidden");
      const body = (await request.json()) as { room_code: string };
      const key = request.headers.get("idempotency-key");
      const repeated = s.invites.some((i) => i.key !== null && i.key === key);
      if (!repeated) s.invites.push({ to: String(params.id), room_code: body.room_code, key });
      return json({ invite_id: s.id("inv"), repeated });
    }),

    http.post(`${base}/me/presence`, ({ request }) => {
      const err = guard(request, true);
      if (err) return err;
      s.presenceCalls += 1;
      return empty();
    }),

    // ---------------------------------------------------------------- заявки
    http.get(`${base}/me/friend-requests`, ({ request }) => {
      const err = guard(request);
      if (err) return err;
      return json({ incoming: s.incoming, outgoing: s.outgoing });
    }),

    http.post(`${base}/me/friend-requests`, async ({ request }) => {
      const err = guard(request, true);
      if (err) return err;
      const key = request.headers.get("idempotency-key");
      if (key && s.idempotency.has(key)) return json(s.idempotency.get(key));
      const body = (await request.json()) as {
        nickname?: string;
        code?: string;
        user_id?: string;
      };
      if ([body.nickname, body.code, body.user_id].filter(Boolean).length !== 1)
        return problem(422, "validation.failed");
      const target = body.user_id
        ? (Object.values(SOCIAL_USERS).find((u) => u.id === body.user_id) ?? {
            id: body.user_id,
            nickname: "Игрок",
          })
        : body.code
          ? body.code === "ZZZZ9999"
            ? SOCIAL_USERS.dmitry
            : null
          : (Object.values(SOCIAL_USERS).find((u) => u.nickname === body.nickname) ?? null);
      if (!target) return problem(404, "social.player_not_found");
      if (s.isFriend(target.id)) return problem(409, "social.already_friends");
      if (s.outgoing.some((r) => r.user_id === target.id))
        return problem(409, "social.request_pending");
      if (s.isBlocked(target.id)) return problem(403, "social.blocked_by_you");
      const view: FriendRequestView = {
        id: s.id("req"),
        user_id: target.id,
        nickname: target.nickname,
        created_at: new Date().toISOString(),
      };
      s.outgoing.push(view);
      if (key) s.idempotency.set(key, view);
      return json(view, 201);
    }),

    http.delete(`${base}/me/friend-requests/:id`, ({ request, params }) => {
      const err = guard(request, true);
      if (err) return err;
      s.outgoing = s.outgoing.filter((r) => r.id !== String(params.id));
      return empty();
    }),

    http.post(`${base}/me/friend-requests/:id/accept`, ({ request, params }) => {
      const err = guard(request, true);
      if (err) return err;
      const req = s.incoming.find((r) => r.id === String(params.id));
      if (!req) return problem(404, "social.request_not_found");
      s.incoming = s.incoming.filter((r) => r !== req);
      s.friends.push({
        user_id: req.user_id,
        nickname: req.nickname,
        status: "offline",
        friends_since: new Date().toISOString(),
      });
      return empty();
    }),

    http.post(`${base}/me/friend-requests/:id/decline`, ({ request, params }) => {
      const err = guard(request, true);
      if (err) return err;
      s.incoming = s.incoming.filter((r) => r.id !== String(params.id));
      return empty();
    }),

    http.get(`${base}/me/friend-code`, ({ request }) => {
      const err = guard(request);
      if (err) return err;
      return json({ code: s.code, rotated_at: DAY });
    }),

    http.post(`${base}/me/friend-code/regenerate`, ({ request }) => {
      const err = guard(request, true);
      if (err) return err;
      s.code = s.code === "ABCD2345" ? "WXYZ6789" : "ABCD2345";
      return json({ code: s.code, rotated_at: new Date().toISOString() });
    }),

    // ---------------------------------------------------------------- блокировки, приватность
    http.get(`${base}/me/blocks`, ({ request }) => {
      const err = guard(request);
      if (err) return err;
      return json({ items: s.blocks });
    }),

    http.put(`${base}/me/blocks/:id`, ({ request, params }) => {
      const err = guard(request, true);
      if (err) return err;
      const id = String(params.id);
      if (id === viewerId(request)) return problem(422, "social.cannot_block_self");
      if (!s.isBlocked(id)) {
        s.blocks.push({
          user_id: id,
          nickname: s.nicknameOf(id) ?? "Игрок",
          created_at: new Date().toISOString(),
        });
        s.friends = s.friends.filter((f) => f.user_id !== id);
      }
      return empty();
    }),

    http.delete(`${base}/me/blocks/:id`, ({ request, params }) => {
      const err = guard(request, true);
      if (err) return err;
      s.blocks = s.blocks.filter((b) => b.user_id !== String(params.id));
      return empty();
    }),

    http.get(`${base}/me/privacy`, ({ request }) => {
      const err = guard(request);
      if (err) return err;
      return json(s.privacy);
    }),

    http.put(`${base}/me/privacy`, async ({ request }) => {
      const err = guard(request, true);
      if (err) return err;
      s.privacy = (await request.json()) as PrivacySettings;
      return json(s.privacy);
    }),

    // ---------------------------------------------------------------- комментарии
    http.get(`${base}/collections/:id/comments`, ({ request, params }) => {
      const url = new URL(request.url);
      return json(
        commentPage(world, String(params.id), url.searchParams.get("cursor"), subjectOf(request)),
      );
    }),

    http.post(`${base}/collections/:id/comments`, async ({ request, params }) => {
      const err = guard(request, true);
      if (err) return err;
      const code = world.scenario.social?.commentError;
      if (code === "comments_rate_limit") return problem(429, "social.comments_rate_limit");
      if (code === "comment_rejected") return problem(422, "social.comment_rejected");
      if (code) return problem(403, `social.${code}`);
      const key = request.headers.get("idempotency-key");
      if (key && s.idempotency.has(key)) return json(s.idempotency.get(key), 201);
      const { body } = (await request.json()) as { body: string };
      const me = viewerId(request)!;
      const collectionId = String(params.id);
      const comment: CommentView = {
        id: s.id("cmt"),
        collection_id: collectionId,
        author_id: me,
        author_nickname: "Игрок",
        body,
        created_at: new Date().toISOString(),
        edited_at: null,
        can_edit: true,
        can_delete: true,
      };
      s.comments.set(collectionId, [...s.commentsOf(collectionId), comment]);
      if (key) s.idempotency.set(key, comment);
      return json(comment, 201);
    }),

    http.patch(`${base}/comments/:id`, async ({ request, params }) => {
      const err = guard(request, true);
      if (err) return err;
      const me = viewerId(request);
      for (const list of s.comments.values()) {
        const c = list.find((x) => x.id === String(params.id));
        if (!c) continue;
        if (c.author_id !== me) return problem(404, "social.comment_not_found");
        if (Date.now() - new Date(c.created_at).getTime() > 5 * 60_000)
          return problem(409, "social.comment_edit_expired");
        c.body = ((await request.json()) as { body: string }).body;
        c.edited_at = new Date().toISOString();
        return json(viewOf(world, c, subjectOf(request)));
      }
      return problem(404, "social.comment_not_found");
    }),

    http.delete(`${base}/comments/:id`, ({ request, params }) => {
      const err = guard(request, true);
      if (err) return err;
      for (const [k, list] of s.comments)
        s.comments.set(
          k,
          list.filter((x) => x.id !== String(params.id)),
        );
      return empty();
    }),

    // ---------------------------------------------------------------- жалобы и санкции
    http.get(`${base}/report-reasons`, ({ request }) => {
      const err = guard(request);
      if (err) return err;
      return json({ items: REASONS });
    }),

    http.post(`${base}/reports`, async ({ request }) => {
      const err = guard(request, true);
      if (err) return err;
      if (world.scenario.social?.reportRateLimited) {
        const res = problem(429, "rate_limited");
        res.headers.set("Retry-After", "60");
        return res;
      }
      const body = (await request.json()) as {
        target_kind: string;
        target_id: string;
        context_match_id?: string | null;
      };
      s.reportBodies.push({ ...body, key: request.headers.get("idempotency-key") });
      if (body.target_id === viewerId(request)) return problem(422, "social.report_self");
      if (body.target_kind === "player" && !body.context_match_id)
        return problem(422, "social.report_no_shared_match");
      const k = `${body.target_kind}:${body.target_id}`;
      const repeated = s.reports.has(k);
      if (!repeated) s.reports.set(k, s.id("rep"));
      return json({ id: s.reports.get(k), repeated });
    }),

    http.get(`${base}/me/sanctions`, ({ request }) => {
      const err = guard(request);
      if (err) return err;
      return json({ items: (world.scenario.social?.sanctions ?? []).map(sanctionOf) });
    }),
  ];
}
