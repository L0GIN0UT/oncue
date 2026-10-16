/**
 * Состояние конструктора сборника (D2): локальное тело черновика, ревизия сервера, под которую
 * оно правится, и конфликт устройств. Чистый редьюсер — автосохранение и сеть снаружи.
 *
 * «Грязность» считается счётчиками правок: сохранение запоминает, сколько правок оно унесло
 * (`inflight`), поэтому правка во время запроса не теряется — после ответа черновик остаётся
 * несохранённым и уходит следующим сохранением.
 */
import type { Domain, DraftBody, DraftItem, DraftOut } from "@/shared/api/types";
import type { FieldError } from "../errors";

export type DraftState = {
  collectionId: string;
  domain: Domain;
  status: DraftOut["status"];
  /** Тело, которое правит автор. */
  body: DraftBody;
  /** Ревизия сервера, поверх которой правится `body` (`base_revision` следующего PUT). */
  revision: number;
  updatedAt: string | null;
  edits: number;
  savedEdits: number;
  inflight: number | null;
  /** Код последней ошибки сохранения (сеть, лимиты); конфликт — отдельно. */
  saveError: string | null;
  /** Актуальный черновик «с другого устройства» (409 `collections.draft_conflict`). */
  conflict: DraftOut | null;
  /** Ошибки публикации по полям. */
  errors: FieldError[];
};

export type DraftAction =
  | { type: "edit"; patch: Partial<Omit<DraftBody, "items">> }
  | { type: "addItem"; mediaItemId: string }
  | { type: "removeItem"; index: number }
  | { type: "moveItem"; from: number; to: number }
  | { type: "setEmoji"; index: number; emoji: string | null }
  | { type: "saveStarted" }
  | { type: "saveSucceeded"; draft: DraftOut }
  | { type: "saveFailed"; code: string }
  | { type: "conflict"; current: DraftOut }
  | { type: "keepMine" }
  | { type: "takeTheirs" }
  | { type: "publishFailed"; errors: FieldError[] }
  | { type: "published" };

/** Тело с заполненными необязательными полями (сервер их может опустить). */
export function normalizeBody(body: DraftBody): DraftBody {
  return {
    title: body.title ?? "",
    description: body.description ?? null,
    tags: [...(body.tags ?? [])],
    cover: body.cover ?? null,
    default_mode_key: body.default_mode_key ?? null,
    visibility: body.visibility ?? "public",
    items: (body.items ?? []).map((i) => ({
      media_item_id: i.media_item_id,
      custom: i.custom?.emoji ? { emoji: i.custom.emoji } : null,
    })),
  };
}

export function sameBody(a: DraftBody, b: DraftBody): boolean {
  return JSON.stringify(normalizeBody(a)) === JSON.stringify(normalizeBody(b));
}

/** Локальная копия черновика (localStorage): тело и ревизия, поверх которой оно правилось. */
export type LocalCopy = { body: DraftBody; baseRevision: number };

/**
 * Начальное состояние по черновику сервера и локальной копии. Копия с той же ревизией и другим
 * телом — несохранённые правки (продолжаем как «грязные»); копия со старой ревизией и другим
 * телом — конфликт: сервер ушёл вперёд, автор выбирает версию.
 */
export function initDraft(draft: DraftOut, local: LocalCopy | null = null): DraftState {
  const server = normalizeBody(draft.body);
  const base: DraftState = {
    collectionId: draft.collection_id,
    domain: draft.domain,
    status: draft.status,
    body: server,
    revision: draft.revision,
    updatedAt: draft.updated_at,
    edits: 0,
    savedEdits: 0,
    inflight: null,
    saveError: null,
    conflict: null,
    errors: [],
  };
  if (!local || sameBody(local.body, server)) return base;
  const mine = normalizeBody(local.body);
  if (local.baseRevision === draft.revision) return { ...base, body: mine, edits: 1 };
  return { ...base, body: mine, edits: 1, conflict: draft };
}

export const isDirty = (s: DraftState) => s.edits !== s.savedEdits;

const touch = (s: DraftState, body: DraftBody): DraftState => ({
  ...s,
  body,
  edits: s.edits + 1,
});

function moveItem(items: DraftItem[], from: number, to: number): DraftItem[] {
  if (from === to || from < 0 || from >= items.length) return items;
  const target = Math.max(0, Math.min(items.length - 1, to));
  const next = [...items];
  const [moved] = next.splice(from, 1);
  next.splice(target, 0, moved!);
  return next;
}

export function draftReducer(s: DraftState, a: DraftAction): DraftState {
  switch (a.type) {
    case "edit":
      return touch(s, { ...s.body, ...a.patch });
    case "addItem": {
      const items = s.body.items ?? [];
      if (items.some((i) => i.media_item_id === a.mediaItemId)) return s;
      return touch(s, {
        ...s.body,
        items: [...items, { media_item_id: a.mediaItemId, custom: null }],
      });
    }
    case "removeItem":
      return touch(s, { ...s.body, items: (s.body.items ?? []).filter((_, i) => i !== a.index) });
    case "moveItem": {
      const items = s.body.items ?? [];
      const next = moveItem(items, a.from, a.to);
      return next === items ? s : touch(s, { ...s.body, items: next });
    }
    case "setEmoji": {
      const items = (s.body.items ?? []).map((item, i) =>
        i === a.index ? { ...item, custom: a.emoji ? { emoji: a.emoji } : null } : item,
      );
      return touch(s, { ...s.body, items });
    }
    case "saveStarted":
      return { ...s, inflight: s.edits, saveError: null };
    case "saveSucceeded":
      return {
        ...s,
        revision: a.draft.revision,
        updatedAt: a.draft.updated_at,
        status: a.draft.status,
        savedEdits: s.inflight ?? s.edits,
        inflight: null,
      };
    case "saveFailed":
      return { ...s, inflight: null, saveError: a.code };
    case "conflict":
      return { ...s, inflight: null, conflict: a.current };
    case "keepMine":
      // Своя версия поверх актуальной ревизии: следующий PUT уйдёт с base_revision сервера.
      if (!s.conflict) return s;
      return { ...s, revision: s.conflict.revision, conflict: null, edits: s.edits + 1 };
    case "takeTheirs": {
      if (!s.conflict) return s;
      const c = s.conflict;
      return {
        ...s,
        body: normalizeBody(c.body),
        revision: c.revision,
        updatedAt: c.updated_at,
        status: c.status,
        conflict: null,
        savedEdits: s.edits,
        errors: [],
      };
    }
    case "publishFailed":
      return { ...s, errors: a.errors };
    case "published":
      // После публикации черновика нет: GET …/draft отдаёт тело версии и `revision = 0`.
      return { ...s, status: "published", revision: 0, errors: [], savedEdits: s.edits };
  }
}
