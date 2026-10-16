/**
 * Мок сборников и медиатеки этапа 3b по контрактам (contracts/openapi/{collections,library}.json,
 * страницы bff_web §2.5 плана): каталог, черновики с ревизиями и конфликтом (D2), публикация с
 * ошибками по полям, лайки и избранное, «пройден» и видимость состава (D6), полки с
 * `exclusive_with` (D11, D12). Логика упрощена, инварианты контракта соблюдены.
 */
import type {
  CollectionDetail,
  CollectionModeCard,
  CollectionsMeta,
  DraftBody,
  DraftOut,
  ModeOut,
  MyCollection,
  ShelfOut,
} from "@/shared/api/types";
import { ITEMS, itemById, type Domain } from "./world";

const uuid = (n: number) => `00000000-0000-4000-a000-${String(n).padStart(12, "0")}`;

export const PLAYER_ID = "00000000-0000-4000-8000-000000000901";
export const AUTHOR_ID = "00000000-0000-4000-8000-000000000902";

const FRAME_LEVELS = [48, 32, 20, 12, 6, 0].map((blur_px, i) => ({
  index: i + 1,
  hint: { blur_px, crop: [0.35, 0.5, 0.7, 1, 1, 1][i] ?? 1 },
}));
const AUDIO_LEVELS = [0.5, 1, 2, 4, 8, 15].map((seconds, i) => ({
  index: i + 1,
  hint: { seconds },
}));
const EMOJI_LEVELS = [1, 2, 3, 4, 6, null].map((count, i) => ({ index: i + 1, hint: { count } }));

const ROUND_OPTIONS: NonNullable<ModeOut["round_options"]> = [5, 10, 25, "all"];
const MIN_ITEMS = 5;

const template = (key: string, clue_type: string, levels: ModeOut["levels"]): ModeOut => ({
  key,
  version: 1,
  domain: key.startsWith("music.") ? "music" : "film",
  title_key: `modes.${key}.title`,
  source_type: "collection",
  clue_type,
  rounds: 200,
  levels,
  allow_guest: true,
  answer_format: "classic",
  presentation: "score",
  start: null,
  presets: null,
  round_options: ROUND_OPTIONS,
  min_items: MIN_ITEMS,
});

/** Шаблоны режимов сборников: `GET /v1/modes?kind=collection` (в `kind=play` их нет). */
export const COLLECTION_MODES: ModeOut[] = [
  template("film.collection.frame", "frame_progressive", FRAME_LEVELS),
  template("film.collection.emoji", "emoji", EMOJI_LEVELS),
  template("music.collection.intro", "audio_intro", AUDIO_LEVELS),
  template("music.collection.emoji", "emoji", EMOJI_LEVELS),
];
/** Без каталожных эмодзи в моке: в режиме эмодзи играбельны только единицы со своими эмодзи. */
const catalogPlayable = (mode: ModeOut, id: string) =>
  mode.clue_type !== "emoji" && itemById(id)?.domain === mode.domain;

export const META: CollectionsMeta = {
  service: "collections",
  status: "ok",
  limits: {
    title_max: 80,
    description_max: 500,
    items_min: 5,
    items_max: 200,
    tags_max: 5,
    drafts_max: 20,
    publish_per_day: 5,
    emoji_min_graphemes: 6,
    emoji_max_graphemes: 12,
  },
  cover_presets: [
    { id: "night", gradient: ["#0f2027", "#203a43", "#2c5364"], emoji: "🌙" },
    { id: "cinema", gradient: ["#232526", "#414345"], emoji: "🎬" },
    { id: "vinyl", gradient: ["#41295a", "#2f0743"], emoji: "🎵" },
    { id: "popcorn", gradient: ["#f7971e", "#ffd200"], emoji: "🍿" },
  ],
  tags: [
    { key: "classic", title_key: "collections.tags.classic", domains: ["music", "film"] },
    { key: "soviet", title_key: "collections.tags.soviet", domains: ["music", "film"] },
    { key: "comedy", title_key: "collections.tags.comedy", domains: ["film"] },
    { key: "rock", title_key: "collections.tags.rock", domains: ["music"] },
  ],
};

type Item = { media_item_id: string; emoji: string | null };

export type MockCollection = {
  id: string;
  domain: Domain;
  kind: "ugc" | "curated";
  authorId: string | null;
  status: "draft" | "published" | "hidden" | "deleted";
  visibility: "public" | "unlisted";
  version: number;
  title: string;
  description: string | null;
  tags: string[];
  cover: string | null;
  modeKey: string;
  items: Item[];
  likes: Set<string>;
  favorites: Set<string>;
  completed: Set<string>;
  plays: number;
  featured: boolean;
  publishedAt: string;
  updatedAt: string;
  /** Черновик: ревизия 0 — черновика нет (тело опубликованной версии). */
  draft: { revision: number; body: DraftBody; label: string | null; updatedAt: string } | null;
  seq: number;
};

const T0 = "2026-10-01T10:00:00Z";

function seedCollections(): MockCollection[] {
  // Единицы — из world.ts; читаются при вызове, а не при загрузке модуля (циклический импорт).
  const film = ITEMS.filter((i) => i.domain === "film").map((i) => i.id);
  const music = ITEMS.filter((i) => i.domain === "music").map((i) => i.id);
  const base = (c: Partial<MockCollection> & Pick<MockCollection, "id" | "domain" | "title">) =>
    ({
      kind: "ugc",
      authorId: AUTHOR_ID,
      status: "published",
      visibility: "public",
      version: 1,
      description: null,
      tags: [],
      cover: null,
      modeKey: `${c.domain}.collection.${c.domain === "film" ? "frame" : "intro"}`,
      items: [],
      likes: new Set<string>(),
      favorites: new Set<string>(),
      completed: new Set<string>(),
      plays: 0,
      featured: false,
      publishedAt: T0,
      updatedAt: T0,
      draft: null,
      seq: 1,
      ...c,
    }) as MockCollection;
  const items = (ids: string[], emoji: Record<number, string> = {}) =>
    ids.map((id, i) => ({ media_item_id: id, emoji: emoji[i] ?? null }));
  return [
    base({
      id: uuid(1),
      domain: "film",
      kind: "curated",
      authorId: null,
      title: "Кино · кадры: классика",
      description: "Угадайте фильм по кадру.",
      tags: ["classic"],
      cover: "cinema",
      featured: true,
      items: items(film.slice(0, 8)),
      plays: 12,
    }),
    base({
      id: uuid(2),
      domain: "film",
      title: "Тарковский и компания",
      description: "Фильмы Андрея Тарковского.",
      tags: ["soviet"],
      cover: "night",
      modeKey: "film.collection.emoji",
      items: items(
        ITEMS.filter((i) => i.person.name === "Андрей Тарковский").map((i) => i.id),
        { 0: "🌊🚀🧠🌌👁️🕯️" },
      ),
      likes: new Set(["user.x1", "user.x2"]),
      publishedAt: "2026-10-05T10:00:00Z",
    }),
    base({
      id: uuid(3),
      domain: "music",
      kind: "curated",
      authorId: null,
      title: "Русский рок",
      tags: ["rock"],
      cover: "vinyl",
      items: items(music.slice(0, 6)),
      publishedAt: "2026-10-03T10:00:00Z",
    }),
    // Сборник игрока `player`, скрытый модератором (плашка в «Моих сборниках»).
    base({
      id: uuid(4),
      domain: "film",
      authorId: PLAYER_ID,
      status: "hidden",
      title: "Мой скрытый сборник",
      items: items(film.slice(2, 7)),
    }),
  ];
}

export type MockShelf = {
  ref: string;
  kind: "system" | "custom";
  key: string | null;
  title: string | null;
  domains: Domain[];
  exclusive: string[];
};

const SYSTEM_SHELVES: MockShelf[] = [
  {
    ref: "system:watch_later",
    kind: "system",
    key: "watch_later",
    title: null,
    domains: ["film"],
    exclusive: ["watched"],
  },
  {
    ref: "system:watched",
    kind: "system",
    key: "watched",
    title: null,
    domains: ["film"],
    exclusive: ["watch_later"],
  },
  {
    ref: "system:favorite",
    kind: "system",
    key: "favorite",
    title: null,
    domains: ["music", "film"],
    exclusive: [],
  },
];

export type UgcScenario = {
  /** Следующий PUT черновика — 409: черновик успели сохранить «с другого устройства». */
  draftConflictOnce?: boolean;
  /** Следующий лайк — 503 (откат оптимистичного лайка). */
  likeFailsOnce?: boolean;
  /** Единица заблокирована в каталоге (на полках — `unavailable`). */
  blockedItem?: string;
};

type Entry = { itemId: string; source: string; addedAt: string };

export class UgcWorld {
  collections: MockCollection[] = seedCollections();
  /** Полки пользователя: субъект → ref → единицы (новые в конце). */
  shelves = new Map<string, Map<string, Entry[]>>();
  customShelves = new Map<string, MockShelf[]>();
  scenario: UgcScenario = {};
  private seq = 0;
  now: () => Date = () => new Date();

  reset(): void {
    this.collections = seedCollections();
    this.shelves.clear();
    this.customShelves.clear();
    this.scenario = {};
    this.seq = 0;
  }

  private id(prefix: number): string {
    this.seq += 1;
    return `0192b000-0000-7000-8${String(prefix).padStart(3, "0")}-${String(this.seq).padStart(12, "0")}`;
  }

  find(id: string): MockCollection | undefined {
    return this.collections.find((c) => c.id === id && c.status !== "deleted");
  }

  isAuthor(c: MockCollection, subject: string | null): boolean {
    return Boolean(subject && c.authorId && subject === `user.${c.authorId}`);
  }

  /** Сборник виден: опубликованный (public/unlisted) — всем, скрытый и черновик — автору. */
  visible(c: MockCollection, subject: string | null): boolean {
    if (c.status === "published") return true;
    return (c.status === "hidden" || c.status === "draft") && this.isAuthor(c, subject);
  }

  nickname(userId: string | null, users: { id: string; nickname: string }[]) {
    return users.find((u) => u.id === userId)?.nickname ?? null;
  }

  private author(c: MockCollection, users: { id: string; nickname: string }[]) {
    return c.kind === "curated"
      ? { kind: "curator" as const, user_id: null, nickname: null }
      : { kind: "user" as const, user_id: c.authorId, nickname: this.nickname(c.authorId, users) };
  }

  card(c: MockCollection, users: { id: string; nickname: string }[]) {
    return {
      collection_id: c.id,
      domain: c.domain,
      kind: c.kind,
      title: c.title,
      cover: c.cover ? { preset: c.cover } : null,
      tags: c.tags,
      author: this.author(c, users),
      default_mode_key: c.modeKey,
      items_count: c.items.length,
      likes_count: c.likes.size,
      plays_count: c.plays,
      difficulty: c.plays >= 10 ? ("medium" as const) : null,
      featured: c.featured,
      published_at: c.publishedAt,
    };
  }

  me(c: MockCollection, subject: string | null) {
    if (!subject?.startsWith("user.")) return null;
    return {
      liked: c.likes.has(subject),
      favorite: c.favorites.has(subject),
      completed: c.completed.has(subject),
      is_author: this.isAuthor(c, subject),
    };
  }

  /** Состав — автору и прошедшему (D6), свои эмодзи — только автору. */
  detail(
    c: MockCollection,
    subject: string | null,
    users: { id: string; nickname: string }[],
  ): CollectionDetail {
    const author = this.isAuthor(c, subject);
    const open = author || Boolean(subject && c.completed.has(subject));
    return {
      ...this.card(c, users),
      description: c.description,
      visibility: c.visibility,
      status: c.status === "hidden" ? "hidden" : "published",
      version: c.version,
      items_visible: open,
      items: open
        ? c.items.map((item, position) => {
            const i = itemById(item.media_item_id);
            return {
              position,
              media_item_id: item.media_item_id,
              title: i?.title ?? "—",
              year: i?.year ?? null,
              available: item.media_item_id !== this.scenario.blockedItem,
              custom: author && item.emoji ? { emoji: item.emoji } : null,
            };
          })
        : null,
      me: this.me(c, subject),
    };
  }

  /** Каталог: опубликованные публичные, поиск по названию, описанию и тегам (не по единицам). */
  catalog(p: URLSearchParams, users: { id: string; nickname: string }[]) {
    const q = (p.get("q") ?? "").trim().toLowerCase();
    const sort = p.get("sort") ?? "popular";
    const limit = Math.min(50, Number(p.get("limit") ?? 20));
    const offset = Number(p.get("cursor")?.replace(/^o/, "") ?? 0) || 0;
    const domain = p.get("domain");
    const mode = p.get("mode");
    const curated = p.get("curated") === "true";
    const all = this.collections
      .filter((c) => c.status === "published" && c.visibility === "public")
      .filter((c) => !domain || c.domain === domain)
      .filter((c) => !mode || c.modeKey === mode)
      .filter((c) => !curated || c.kind === "curated")
      .filter((c) => sort !== "featured" || c.featured)
      .filter(
        (c) =>
          !q ||
          [c.title, c.description ?? "", ...c.tags.map((k) => k)].some((s) =>
            s.toLowerCase().includes(q),
          ),
      )
      .sort((a, b) =>
        sort === "new"
          ? b.publishedAt.localeCompare(a.publishedAt)
          : sort === "likes"
            ? b.likes.size - a.likes.size
            : b.plays + b.likes.size - (a.plays + a.likes.size),
      );
    const page = all.slice(offset, offset + limit);
    return {
      items: page.map((c) => this.card(c, users)),
      next_cursor: offset + limit < all.length ? `o${offset + limit}` : null,
    };
  }

  draftOut(c: MockCollection): DraftOut {
    const body: DraftBody = c.draft?.body ?? this.publishedBody(c);
    return {
      collection_id: c.id,
      domain: c.domain,
      status: c.status === "deleted" ? "draft" : c.status,
      revision: c.draft?.revision ?? 0,
      base_version: c.status === "draft" ? null : c.version,
      client_label: c.draft?.label ?? null,
      updated_at: c.draft?.updatedAt ?? c.updatedAt,
      body,
    };
  }

  publishedBody(c: MockCollection): DraftBody {
    return {
      title: c.title,
      description: c.description,
      tags: [...c.tags],
      cover: c.cover ? { preset: c.cover } : null,
      default_mode_key: c.modeKey,
      visibility: c.visibility,
      items: c.items.map((i) => ({
        media_item_id: i.media_item_id,
        custom: i.emoji ? { emoji: i.emoji } : null,
      })),
    };
  }

  create(subject: string, domain: Domain, title: string): MockCollection {
    const now = this.now().toISOString();
    const c: MockCollection = {
      id: this.id(5),
      domain,
      kind: "ugc",
      authorId: subject.slice("user.".length),
      status: "draft",
      visibility: "public",
      version: 0,
      title,
      description: null,
      tags: [],
      cover: null,
      modeKey: "",
      items: [],
      likes: new Set(),
      favorites: new Set(),
      completed: new Set(),
      plays: 0,
      featured: false,
      publishedAt: now,
      updatedAt: now,
      draft: {
        revision: 1,
        label: null,
        updatedAt: now,
        body: {
          title,
          description: null,
          tags: [],
          cover: null,
          default_mode_key: null,
          visibility: "public",
          items: [],
        },
      },
      seq: 1,
    };
    this.collections.push(c);
    return c;
  }

  /** PUT черновика с оптимистичной блокировкой: `null` — сохранено, иначе — конфликт. */
  saveDraft(
    c: MockCollection,
    baseRevision: number,
    body: DraftBody,
    label: string | null,
  ): DraftOut | "conflict" {
    const current = c.draft?.revision ?? 0;
    if (this.scenario.draftConflictOnce) {
      this.scenario.draftConflictOnce = false;
      this.bumpFromOtherDevice(c);
      return "conflict";
    }
    if (baseRevision !== current) return "conflict";
    c.seq += 1;
    c.draft = { revision: c.seq, body, label, updatedAt: this.now().toISOString() };
    return this.draftOut(c);
  }

  /** Сохранение «с другого устройства»: новая ревизия и другое название. */
  bumpFromOtherDevice(c: MockCollection): void {
    const body = c.draft?.body ?? this.publishedBody(c);
    c.seq += 1;
    c.draft = {
      revision: c.seq,
      body: { ...body, title: `${body.title} (планшет)` },
      label: "Safari · iOS",
      updatedAt: this.now().toISOString(),
    };
  }

  /** Проверка публикации (как `CollectionPolicy.publishable`): все ошибки сразу. */
  publishErrors(c: MockCollection, body: DraftBody) {
    const errors: { path: string; code: string; reason?: string; min?: number }[] = [];
    const limits = META.limits!;
    const title = body.title.trim();
    if (!title)
      errors.push({ path: "title", code: "collections.title_invalid", reason: "ugc.empty" });
    else if (/https?:\/\/|www\./i.test(title))
      errors.push({
        path: "title",
        code: "collections.title_invalid",
        reason: "ugc.link_forbidden",
      });
    if (!body.default_mode_key || !body.default_mode_key.startsWith(`${c.domain}.collection.`))
      errors.push({ path: "default_mode_key", code: "collections.mode_invalid" });
    const items = body.items ?? [];
    if (items.length < limits.items_min)
      errors.push({ path: "items", code: "collections.items_too_few", min: limits.items_min });
    items.forEach((item, i) => {
      if (item.media_item_id === this.scenario.blockedItem)
        errors.push({ path: `items[${i}]`, code: "collections.item_unavailable" });
      const emoji = item.custom?.emoji;
      if (emoji && /[\p{L}\p{N}\s]/u.test(emoji))
        errors.push({ path: `items[${i}].custom.emoji`, code: "collections.emoji_invalid" });
      else if (
        emoji &&
        [...new Intl.Segmenter().segment(emoji)].length < limits.emoji_min_graphemes
      )
        errors.push({
          path: `items[${i}].custom.emoji`,
          code: "collections.emoji_too_short",
          min: limits.emoji_min_graphemes,
        });
    });
    return errors;
  }

  publish(c: MockCollection): void {
    const body = c.draft!.body;
    c.title = body.title.trim();
    c.description = body.description ?? null;
    c.tags = [...(body.tags ?? [])];
    c.cover = body.cover?.preset ?? null;
    c.modeKey = body.default_mode_key!;
    c.visibility = body.visibility;
    c.items = (body.items ?? []).map((i) => ({
      media_item_id: i.media_item_id,
      emoji: i.custom?.emoji ?? null,
    }));
    c.version += 1;
    if (c.status === "draft") {
      c.status = "published";
      c.publishedAt = this.now().toISOString();
    }
    c.updatedAt = this.now().toISOString();
    c.completed.clear();
    c.draft = null;
  }

  mine(subject: string): MyCollection[] {
    return this.collections
      .filter((c) => c.status !== "deleted" && this.isAuthor(c, subject))
      .map((c) => ({
        collection_id: c.id,
        domain: c.domain,
        kind: c.kind,
        status: c.status === "deleted" ? "draft" : c.status,
        title: c.draft?.body.title ?? c.title,
        items_count: (c.draft?.body.items ?? c.items).length,
        likes_count: c.likes.size,
        has_draft: Boolean(c.draft),
        draft_revision: c.draft?.revision ?? null,
        hidden_by_moderator: c.status === "hidden",
        published_at: c.status === "draft" ? null : c.publishedAt,
        version: c.status === "draft" ? null : c.version,
        visibility: c.visibility,
        updated_at: c.draft?.updatedAt ?? c.updatedAt,
      }));
  }

  // ------------------------------------------------------------------ game: режимы сборника

  modes(c: MockCollection): CollectionModeCard[] {
    return COLLECTION_MODES.filter((m) => m.domain === c.domain).map((m) => {
      const playable = this.playableItems(c, m).length;
      return {
        mode_key: m.key,
        title_key: m.title_key,
        clue_type: m.clue_type,
        allow_guest: m.allow_guest,
        playable,
        total: c.items.length,
        available: playable >= (m.min_items ?? MIN_ITEMS),
        round_options: m.round_options ?? [],
      };
    });
  }

  /** Играбельные единицы: каталожный набор или свои эмодзи сборника (scope, ADR 0029). */
  playableItems(c: MockCollection, m: ModeOut): string[] {
    return c.items
      .filter((i) => i.media_item_id !== this.scenario.blockedItem)
      .filter((i) => catalogPlayable(m, i.media_item_id) || (m.clue_type === "emoji" && i.emoji))
      .map((i) => i.media_item_id);
  }

  playability(m: ModeOut, ids: string[]): string[] {
    return ids.filter((id) => catalogPlayable(m, id) && id !== this.scenario.blockedItem);
  }

  // ------------------------------------------------------------------ медиатека

  shelfList(subject: string): MockShelf[] {
    return [...SYSTEM_SHELVES, ...(this.customShelves.get(subject) ?? [])];
  }

  shelf(subject: string, ref: string): MockShelf | undefined {
    return this.shelfList(subject).find((s) => s.ref === ref);
  }

  entries(subject: string, ref: string): Entry[] {
    let bySubject = this.shelves.get(subject);
    if (!bySubject) this.shelves.set(subject, (bySubject = new Map()));
    let list = bySubject.get(ref);
    if (!list) bySubject.set(ref, (list = []));
    return list;
  }

  shelfOut(subject: string, s: MockShelf): ShelfOut {
    const items = this.entries(subject, s.ref);
    return {
      ref: s.ref,
      shelf_id: s.kind === "custom" ? s.ref : null,
      kind: s.kind,
      key: s.key,
      title: s.title,
      title_key: s.key ? `library.shelf.${s.key}` : null,
      domains: s.domains,
      exclusive_with: s.exclusive,
      items_count: items.length,
      updated_at: items.at(-1)?.addedAt ?? null,
    };
  }

  itemShelves(subject: string, itemId: string): string[] {
    return this.shelfList(subject)
      .filter((s) => this.entries(subject, s.ref).some((e) => e.itemId === itemId))
      .map((s) => s.ref);
  }

  save(subject: string, s: MockShelf, itemId: string, source: string) {
    const list = this.entries(subject, s.ref);
    const changed = !list.some((e) => e.itemId === itemId);
    const removed: string[] = [];
    if (changed) {
      list.push({ itemId, source, addedAt: this.now().toISOString() });
      for (const key of s.exclusive) {
        const other = this.entries(subject, `system:${key}`);
        const at = other.findIndex((e) => e.itemId === itemId);
        if (at >= 0) {
          other.splice(at, 1);
          removed.push(`system:${key}`);
        }
      }
    }
    return {
      media_item_id: itemId,
      changed,
      shelves: this.itemShelves(subject, itemId),
      removed_from: removed,
    };
  }

  remove(subject: string, s: MockShelf, itemId: string) {
    const list = this.entries(subject, s.ref);
    const at = list.findIndex((e) => e.itemId === itemId);
    if (at >= 0) list.splice(at, 1);
    return {
      media_item_id: itemId,
      changed: at >= 0,
      shelves: this.itemShelves(subject, itemId),
      removed_from: [],
    };
  }

  createShelf(subject: string, title: string): MockShelf {
    const shelf: MockShelf = {
      ref: this.id(6),
      kind: "custom",
      key: null,
      title,
      domains: ["music", "film"],
      exclusive: [],
    };
    this.customShelves.set(subject, [...(this.customShelves.get(subject) ?? []), shelf]);
    return shelf;
  }

  deleteShelf(subject: string, ref: string): boolean {
    const list = this.customShelves.get(subject) ?? [];
    const next = list.filter((s) => s.ref !== ref);
    this.customShelves.set(subject, next);
    this.shelves.get(subject)?.delete(ref);
    return next.length !== list.length;
  }

  shelfItems(subject: string, ref: string) {
    return [...this.entries(subject, ref)].reverse().map((e) => {
      const i = itemById(e.itemId)!;
      return {
        media_item_id: i.id,
        domain: i.domain,
        kind: i.domain === "music" ? "track" : "film",
        title: i.title,
        year: i.year,
        persons: [i.person.name],
        status:
          e.itemId === this.scenario.blockedItem
            ? ("unavailable" as const)
            : ("available" as const),
        source: e.source as "reveal",
        added_at: e.addedAt,
      };
    });
  }
}
