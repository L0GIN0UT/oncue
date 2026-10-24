/**
 * Обработчики мок-бэкенда этапа 3b: collections и library (мутации — cookie + CSRF +
 * `Idempotency-Key`, гостю — 403 `auth.guest_forbidden`, D7), режимы сборника и `playability`
 * game, карточка единицы catalog, страницы bff сборников и медиатеки.
 */
import { http, HttpResponse, type HttpHandler } from "msw";
import type { DraftBody } from "@/shared/api/types";
import { csrfOk, problem, subjectOf } from "./handlers";
import type { MockWorld } from "./world";
import { itemById } from "./world";
import { META } from "./ugc";
import { commentsBlock } from "./social";

type Params = Record<string, string | readonly string[] | undefined>;
type Ctx = { request: Request; params: Params };

const ok = (body: unknown, status = 200) =>
  HttpResponse.json(body as object, { status, headers: { "Cache-Control": "no-store" } });

export function createUgcHandlers(world: MockWorld): HttpHandler[] {
  const ugc = world.ugc;
  const c = "*/api/collections/v1";
  const l = "*/api/library/v1";
  const users = () => world.users;

  /** Мутация пользователя: субъект, CSRF, ключ идемпотентности; гостю — 403. */
  const mutation =
    (run: (ctx: Ctx & { subject: string }) => Response | Promise<Response>) => async (ctx: Ctx) => {
      const subject = subjectOf(ctx.request);
      if (!subject) return problem(401, "auth.unauthenticated");
      if (!csrfOk(ctx.request)) return problem(403, "auth.csrf_failed");
      if (!ctx.request.headers.get("idempotency-key"))
        return problem(422, "idempotency.key_required");
      if (!subject.startsWith("user.")) return problem(403, "auth.guest_forbidden");
      return run({ ...ctx, subject });
    };

  /** Чтение только пользователю (медиатека, мои сборники, черновик). */
  const userRead =
    (run: (ctx: Ctx & { subject: string }) => Response | Promise<Response>) => (ctx: Ctx) => {
      const subject = subjectOf(ctx.request);
      if (!subject) return problem(401, "auth.unauthenticated");
      if (!subject.startsWith("user.")) return problem(403, "auth.guest_forbidden");
      return run({ ...ctx, subject });
    };

  const own = (subject: string, id: string) => {
    const col = ugc.find(id);
    return col && ugc.isAuthor(col, subject) ? col : null;
  };

  const collectionPage = (id: string, subject: string | null) => {
    const col = ugc.find(id);
    if (!col || !ugc.visible(col, subject)) return null;
    const detail = ugc.detail(col, subject, users());
    const { me, ...rest } = detail;
    return {
      blocks: {
        collection: { status: "ok", data: rest },
        modes:
          col.status === "published"
            ? { status: "ok", data: ugc.modes(col) }
            : { status: "unavailable", data: null },
        comments: col.status === "published" ? commentsBlock(world, id, subject) : null,
      },
      me: me ?? null,
    };
  };

  return [
    // ---------------------------------------------------------------- collections
    http.get(`${c}/meta`, () => HttpResponse.json(META)),

    http.get(`${c}/collections`, ({ request }) =>
      HttpResponse.json(ugc.catalog(new URL(request.url).searchParams, users())),
    ),

    http.post(
      `${c}/collections`,
      mutation(async ({ request, subject }) => {
        const body = (await request.json()) as { domain: "film" | "music"; title: string };
        if (!body.title?.trim())
          return problem(422, "collections.title_invalid", { reason: "ugc.empty" });
        const col = ugc.create(subject, body.domain, body.title.trim());
        return ok(ugc.draftOut(col), 201);
      }),
    ),

    http.get(`${c}/collections/:id`, ({ request, params }) => {
      const subject = subjectOf(request);
      const col = ugc.find(String(params.id));
      if (!col || !ugc.visible(col, subject) || col.status === "draft")
        return problem(404, "collections.not_found");
      return HttpResponse.json(ugc.detail(col, subject, users()));
    }),

    http.delete(
      `${c}/collections/:id`,
      mutation(({ subject, params }) => {
        const col = own(subject, String(params.id));
        if (col) col.status = "deleted";
        return new HttpResponse(null, { status: 204 });
      }),
    ),

    http.get(
      `${c}/collections/:id/draft`,
      userRead(({ subject, params }) => {
        const col = own(subject, String(params.id));
        if (!col) return problem(404, "collections.not_found");
        return ok(ugc.draftOut(col));
      }),
    ),

    http.put(
      `${c}/collections/:id/draft`,
      mutation(async ({ request, subject, params }) => {
        const col = own(subject, String(params.id));
        if (!col) return problem(404, "collections.not_found");
        if (col.status === "hidden") return problem(409, "collections.hidden");
        const body = (await request.json()) as {
          base_revision: number;
          body: DraftBody;
          client_label?: string | null;
        };
        if ((body.body.items ?? []).length > META.limits!.items_max)
          return problem(422, "collections.draft_invalid", {
            errors: [{ path: "items", code: "collections.items_too_many", max: 200 }],
          });
        const out = ugc.saveDraft(col, body.base_revision, body.body, body.client_label ?? null);
        if (out === "conflict")
          return problem(409, "collections.draft_conflict", { current: ugc.draftOut(col) });
        return ok(out);
      }),
    ),

    http.post(
      `${c}/collections/:id/publish`,
      mutation(async ({ request, subject, params }) => {
        const col = own(subject, String(params.id));
        if (!col) return problem(404, "collections.not_found");
        if (col.status === "hidden") return problem(409, "collections.hidden");
        const { draft_revision } = (await request.json()) as { draft_revision: number };
        if (!col.draft || col.draft.revision !== draft_revision)
          return problem(409, "collections.draft_conflict", { current: ugc.draftOut(col) });
        const errors = ugc.publishErrors(col, col.draft.body);
        if (errors.length) return problem(422, "collections.publish_invalid", { errors });
        ugc.publish(col);
        return ok(ugc.detail(col, subject, users()));
      }),
    ),

    ...(["like", "favorite"] as const).flatMap((kind) =>
      (["put", "delete"] as const).map((method) =>
        http[method](
          `${c}/collections/:id/${kind}`,
          mutation(({ subject, params }) => {
            const col = ugc.find(String(params.id));
            if (!col || col.status !== "published") return problem(404, "collections.not_found");
            if (kind === "like" && ugc.isAuthor(col, subject))
              return problem(403, "collections.own_collection");
            if (kind === "like" && ugc.scenario.likeFailsOnce) {
              ugc.scenario.likeFailsOnce = false;
              return problem(503, "collections.unavailable");
            }
            const set = kind === "like" ? col.likes : col.favorites;
            if (method === "put") set.add(subject);
            else set.delete(subject);
            return ok(
              kind === "like"
                ? { liked: col.likes.has(subject), likes_count: col.likes.size }
                : { favorite: col.favorites.has(subject) },
            );
          }),
        ),
      ),
    ),

    http.get(
      `${c}/me/collections`,
      userRead(({ subject }) => ok({ items: ugc.mine(subject), next_cursor: null })),
    ),

    http.get(
      `${c}/me/favorites`,
      userRead(({ subject }) =>
        ok({
          items: ugc.collections
            .filter((x) => x.status === "published" && x.favorites.has(subject))
            .map((x) => ugc.card(x, users())),
          next_cursor: null,
        }),
      ),
    ),

    // ---------------------------------------------------------------- game: сборники
    http.get("*/api/game/v1/collections/:id/modes", ({ params }) => {
      const col = ugc.find(String(params.id));
      if (!col || col.status !== "published") return problem(404, "game.collection_not_found");
      return HttpResponse.json(ugc.modes(col));
    }),

    http.post(
      "*/api/game/v1/modes/:mode_key/playability",
      userRead(async ({ request, params }) => {
        const mode = world.mode(String(params.mode_key));
        if (!mode) return problem(404, "game.mode_not_found");
        if (mode.source_type !== "collection") return problem(422, "game.collection_not_supported");
        const { media_item_ids } = (await request.json()) as { media_item_ids: string[] };
        return ok({ mode_key: mode.key, playable_ids: ugc.playability(mode, media_item_ids) });
      }),
    ),

    // ---------------------------------------------------------------- catalog: карточка
    http.get("*/api/catalog/v1/items/:id", ({ params }) => {
      const i = itemById(String(params.id));
      if (!i) return problem(404, "catalog.item_not_found");
      return HttpResponse.json({
        id: i.id,
        domain: i.domain,
        kind: i.domain === "music" ? "track" : "film",
        title: i.title,
        original_title: null,
        subtitle: i.person.name,
        year: i.year,
        genres: i.genres,
        persons: [{ person_id: i.id, name: i.person.name, role: i.person.role, order: 0 }],
        metrics: {},
      });
    }),

    // ---------------------------------------------------------------- library
    http.get(
      `${l}/shelves`,
      userRead(({ subject }) => ok(ugc.shelfList(subject).map((s) => ugc.shelfOut(subject, s)))),
    ),

    http.post(
      `${l}/shelves`,
      mutation(async ({ request, subject }) => {
        const { title } = (await request.json()) as { title: string };
        if (!title?.trim()) return problem(422, "library.title_invalid", { reason: "ugc.empty" });
        if (/https?:\/\//i.test(title))
          return problem(422, "library.title_invalid", { reason: "ugc.link_forbidden" });
        return ok(ugc.shelfOut(subject, ugc.createShelf(subject, title.trim())), 201);
      }),
    ),

    http.patch(
      `${l}/shelves/:id`,
      mutation(async ({ request, subject, params }) => {
        const shelf = ugc.shelf(subject, String(params.id));
        if (!shelf || shelf.kind !== "custom") return problem(404, "library.shelf_not_found");
        const { title } = (await request.json()) as { title: string };
        shelf.title = title.trim();
        return ok(ugc.shelfOut(subject, shelf));
      }),
    ),

    http.delete(
      `${l}/shelves/:id`,
      mutation(({ subject, params }) => {
        if (!ugc.deleteShelf(subject, String(params.id)))
          return problem(404, "library.shelf_not_found");
        return new HttpResponse(null, { status: 204 });
      }),
    ),

    http.get(
      `${l}/items/:id/shelves`,
      userRead(({ subject, params }) =>
        ok({
          media_item_id: String(params.id),
          shelves: ugc.itemShelves(subject, String(params.id)),
        }),
      ),
    ),

    http.put(
      `${l}/shelves/:ref/items/:id`,
      mutation(async ({ request, subject, params }) => {
        const shelf = ugc.shelf(subject, String(params.ref));
        if (!shelf) return problem(404, "library.shelf_not_found");
        const item = itemById(String(params.id));
        if (!item || item.id === ugc.scenario.blockedItem)
          return problem(422, "library.item_unavailable");
        if (!shelf.domains.includes(item.domain)) return problem(422, "library.domain_mismatch");
        const { source } = (await request.json()) as { source: string };
        return ok(ugc.save(subject, shelf, item.id, source));
      }),
    ),

    http.delete(
      `${l}/shelves/:ref/items/:id`,
      mutation(({ subject, params }) => {
        const shelf = ugc.shelf(subject, String(params.ref));
        if (!shelf) return problem(404, "library.shelf_not_found");
        return ok(ugc.remove(subject, shelf, String(params.id)));
      }),
    ),

    // ---------------------------------------------------------------- bff-web: страницы 3b
    http.get("*/api/web/v1/pages/collections", ({ request }) => {
      const p = new URL(request.url).searchParams;
      const block = (sort: string) => {
        const q = new URLSearchParams(p);
        q.set("sort", sort);
        q.set("limit", "12");
        return { status: "ok", data: ugc.catalog(q, users()) };
      };
      if (p.get("q") || p.get("cursor"))
        return HttpResponse.json({
          blocks: {
            featured: null,
            popular: null,
            new: null,
            results: { status: "ok", data: ugc.catalog(p, users()) },
          },
        });
      return HttpResponse.json({
        blocks: {
          featured: block("featured"),
          popular: block("popular"),
          new: block("new"),
          results: null,
        },
      });
    }),

    http.get("*/api/web/v1/pages/collections/:id", ({ request, params }) => {
      const page = collectionPage(String(params.id), subjectOf(request));
      if (!page) return problem(404, "collections.not_found");
      return ok(page);
    }),

    http.get("*/api/web/v1/pages/library", ({ request }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.unauthenticated");
      if (!subject.startsWith("user.")) return problem(403, "auth.guest_forbidden");
      return ok({
        blocks: {
          shelves: {
            status: "ok",
            data: ugc.shelfList(subject).map((s) => ugc.shelfOut(subject, s)),
          },
        },
      });
    }),

    http.get("*/api/web/v1/pages/library/:ref", ({ request, params }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.unauthenticated");
      if (!subject.startsWith("user.")) return problem(403, "auth.guest_forbidden");
      const ref = decodeURIComponent(String(params.ref));
      const shelf = ugc.shelf(subject, ref);
      if (!shelf) return problem(404, "library.shelf_not_found");
      return ok({
        blocks: {
          shelf: {
            status: "ok",
            data: {
              shelf: ugc.shelfOut(subject, shelf),
              items: ugc.shelfItems(subject, ref),
              next_cursor: null,
            },
          },
        },
      });
    }),
  ];
}
