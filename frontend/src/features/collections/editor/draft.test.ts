import { describe, expect, it } from "vitest";
import type { DraftOut } from "@/shared/api/types";
import { draftReducer, initDraft, isDirty, type DraftAction, type DraftState } from "./draft";

const draft = (over: Partial<DraftOut> = {}): DraftOut => ({
  collection_id: "c1",
  domain: "film",
  status: "draft",
  revision: 3,
  base_version: null,
  client_label: null,
  updated_at: "2026-10-16T10:00:00Z",
  body: {
    title: "Кино",
    description: null,
    tags: [],
    cover: null,
    default_mode_key: "film.collection.frame",
    visibility: "public",
    items: [
      { media_item_id: "a", custom: null },
      { media_item_id: "b", custom: null },
      { media_item_id: "c", custom: null },
    ],
  },
  ...over,
});

const run = (s: DraftState, ...actions: DraftAction[]) => actions.reduce(draftReducer, s);
const ids = (s: DraftState) => (s.body.items ?? []).map((i) => i.media_item_id);

describe("draftReducer", () => {
  it("правки делают черновик несохранённым, успешное сохранение — сохранённым", () => {
    const s = run(initDraft(draft()), { type: "edit", patch: { title: "Новое" } });
    expect(isDirty(s)).toBe(true);
    const saved = run(
      s,
      { type: "saveStarted" },
      {
        type: "saveSucceeded",
        draft: draft({ revision: 4 }),
      },
    );
    expect(isDirty(saved)).toBe(false);
    expect(saved.revision).toBe(4);
    expect(saved.body.title).toBe("Новое");
  });

  it("правка во время сохранения не теряется: после ответа черновик снова «грязный»", () => {
    const s = run(
      initDraft(draft()),
      { type: "edit", patch: { title: "1" } },
      { type: "saveStarted" },
      { type: "edit", patch: { title: "2" } },
      { type: "saveSucceeded", draft: draft({ revision: 4 }) },
    );
    expect(s.revision).toBe(4);
    expect(isDirty(s)).toBe(true);
    expect(s.body.title).toBe("2");
  });

  it("порядок: перемещение, границы, удаление и без дублей при добавлении", () => {
    let s = run(initDraft(draft()), { type: "moveItem", from: 2, to: 0 });
    expect(ids(s)).toEqual(["c", "a", "b"]);
    s = run(s, { type: "moveItem", from: 0, to: 99 });
    expect(ids(s)).toEqual(["a", "b", "c"]);
    const same = run(s, { type: "addItem", mediaItemId: "a" });
    expect(same).toBe(s);
    s = run(s, { type: "addItem", mediaItemId: "d" }, { type: "removeItem", index: 0 });
    expect(ids(s)).toEqual(["b", "c", "d"]);
  });

  it("свои эмодзи: пустое значение убирает custom", () => {
    let s = run(initDraft(draft()), { type: "setEmoji", index: 1, emoji: "🚀🌌⏳🌽🪐📚" });
    expect(s.body.items?.[1]?.custom).toEqual({ emoji: "🚀🌌⏳🌽🪐📚" });
    s = run(s, { type: "setEmoji", index: 1, emoji: null });
    expect(s.body.items?.[1]?.custom).toBeNull();
  });

  it("конфликт → «моя версия»: тело своё, ревизия сервера, снова есть что сохранить", () => {
    const theirs = draft({
      revision: 7,
      client_label: "Safari · iOS",
      body: { ...draft().body, title: "Чужое" },
    });
    const s = run(
      initDraft(draft()),
      { type: "edit", patch: { title: "Моё" } },
      { type: "saveStarted" },
      { type: "conflict", current: theirs },
    );
    expect(s.conflict).toEqual(theirs);
    const mine = run(s, { type: "keepMine" });
    expect(mine.conflict).toBeNull();
    expect(mine.revision).toBe(7);
    expect(mine.body.title).toBe("Моё");
    expect(isDirty(mine)).toBe(true);
  });

  it("конфликт → «с другого устройства»: тело и ревизия сервера, правок нет", () => {
    const theirs = draft({ revision: 7, body: { ...draft().body, title: "Чужое" } });
    const s = run(
      initDraft(draft()),
      { type: "edit", patch: { title: "Моё" } },
      { type: "conflict", current: theirs },
      { type: "takeTheirs" },
    );
    expect(s.body.title).toBe("Чужое");
    expect(s.revision).toBe(7);
    expect(isDirty(s)).toBe(false);
    expect(s.conflict).toBeNull();
  });

  it("публикация: черновика больше нет (revision 0), ошибки сброшены", () => {
    const s = run(
      initDraft(draft()),
      { type: "publishFailed", errors: [{ path: "title", code: "collections.title_invalid" }] },
      { type: "published" },
    );
    expect(s.status).toBe("published");
    expect(s.revision).toBe(0);
    expect(s.errors).toEqual([]);
  });
});

describe("initDraft и локальная копия", () => {
  it("копия совпадает с сервером — игнорируется", () => {
    const s = initDraft(draft(), { body: draft().body, baseRevision: 1 });
    expect(isDirty(s)).toBe(false);
    expect(s.conflict).toBeNull();
  });

  it("копия той же ревизии с правками — продолжаем несохранённым", () => {
    const local = { body: { ...draft().body, title: "Офлайн-правка" }, baseRevision: 3 };
    const s = initDraft(draft(), local);
    expect(s.body.title).toBe("Офлайн-правка");
    expect(isDirty(s)).toBe(true);
    expect(s.conflict).toBeNull();
  });

  it("копия старой ревизии с правками — сразу экран конфликта", () => {
    const local = { body: { ...draft().body, title: "Офлайн-правка" }, baseRevision: 2 };
    const s = initDraft(draft(), local);
    expect(s.body.title).toBe("Офлайн-правка");
    expect(s.conflict?.revision).toBe(3);
  });
});
