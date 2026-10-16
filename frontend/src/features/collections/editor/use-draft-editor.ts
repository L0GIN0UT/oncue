"use client";
/**
 * Автосохранение черновика (D2): правка → пауза `AUTOSAVE_DEBOUNCE_MS` → `PUT …/draft` поверх
 * `revision`. Сохранения идут строго по одному; 409 `collections.draft_conflict` останавливает
 * автосохранение до выбора версии. Несохранённое тело лежит в локальной копии (localStorage).
 *
 * Состояние держится в ref рядом с `useState`: сохранение и публикация, запущенные подряд,
 * видят свежую ревизию сразу, а не после перерисовки (иначе второй PUT ушёл бы со старой
 * ревизией и сам себе устроил конфликт).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { isApiError } from "@/shared/api/errors";
import type { DraftOut } from "@/shared/api/types";
import { newIdempotencyKey } from "@/shared/lib/id";
import { publishDraft, saveDraft } from "../api";
import { fieldErrorsOf } from "../errors";
import { draftReducer, initDraft, isDirty, type DraftAction, type DraftState } from "./draft";
import { clearLocalCopy, deviceLabel, readLocalCopy, writeLocalCopy } from "./local-copy";

export const AUTOSAVE_DEBOUNCE_MS = 1500;
/** Пауза перед повтором после сетевой ошибки или 5xx. */
export const RETRY_MS = 5000;

const conflictOf = (e: unknown): DraftOut | null => {
  if (!isApiError(e, "collections.draft_conflict")) return null;
  const current = e.details?.current;
  return current && typeof current === "object" ? (current as DraftOut) : null;
};

/** Ошибку стоит повторить автоматически: сеть, 5xx, 429. */
const retryable = (e: unknown) => !isApiError(e) || e.status >= 500 || e.status === 429;

export type PublishOutcome = "published" | "invalid" | "conflict" | "failed";

export function useDraftEditor(draft: DraftOut, debounceMs = AUTOSAVE_DEBOUNCE_MS) {
  const id = draft.collection_id;
  const [state, setState] = useState<DraftState>(() => initDraft(draft, readLocalCopy(id)));
  const ref = useRef(state);
  const pending = useRef<Promise<boolean> | null>(null);
  const label = useRef<string>("");
  const [lastError, setLastError] = useState<unknown>(null);
  const [retryAt, setRetryAt] = useState(0);

  const dispatch = useCallback((a: DraftAction) => {
    ref.current = draftReducer(ref.current, a);
    setState(ref.current);
  }, []);

  /** Сохранить, если есть что; `true` — на сервере актуальное тело. */
  const save = useCallback(async (): Promise<boolean> => {
    while (pending.current) await pending.current;
    const s = ref.current;
    if (s.conflict) return false;
    if (!isDirty(s)) return true;
    dispatch({ type: "saveStarted" });
    label.current ||= deviceLabel();
    const run = (async () => {
      try {
        const out = await saveDraft(id, s.revision, s.body, label.current, newIdempotencyKey());
        dispatch({ type: "saveSucceeded", draft: out });
        setLastError(null);
        return !isDirty(ref.current);
      } catch (e) {
        const current = conflictOf(e);
        if (current) dispatch({ type: "conflict", current });
        else {
          dispatch({ type: "saveFailed", code: isApiError(e) ? e.code : "network" });
          setLastError(e);
          setRetryAt(retryable(e) ? Date.now() + RETRY_MS : Infinity);
        }
        return false;
      }
    })();
    pending.current = run;
    try {
      return await run;
    } finally {
      pending.current = null;
    }
  }, [dispatch, id]);

  // Автосохранение: после паузы в правках; после ошибки — повтор с задержкой или до новой правки.
  const failedEdits = useRef<number | null>(null);
  useEffect(() => {
    if (state.saveError) failedEdits.current ??= state.edits;
    else failedEdits.current = null;
  }, [state.saveError, state.edits]);
  useEffect(() => {
    if (!isDirty(state) || state.conflict || state.inflight !== null) return;
    let delay = debounceMs;
    if (state.saveError && failedEdits.current === state.edits) {
      if (!Number.isFinite(retryAt)) return;
      delay = Math.max(debounceMs, retryAt - Date.now());
    }
    const timer = setTimeout(() => void save(), delay);
    return () => clearTimeout(timer);
  }, [state, debounceMs, save, retryAt]);

  // Локальная копия: есть несохранённое — пишем, всё на сервере — удаляем.
  useEffect(() => {
    if (isDirty(state) || state.conflict)
      writeLocalCopy(id, { body: state.body, baseRevision: state.revision });
    else clearLocalCopy(id);
  }, [id, state]);

  // Уходя со вкладки — сохранить сразу, не дожидаясь паузы.
  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === "hidden") void save();
    };
    document.addEventListener("visibilitychange", onHide);
    return () => document.removeEventListener("visibilitychange", onHide);
  }, [save]);

  const keepMine = useCallback(async () => {
    dispatch({ type: "keepMine" });
    return save();
  }, [dispatch, save]);

  const takeTheirs = useCallback(() => dispatch({ type: "takeTheirs" }), [dispatch]);

  const publish = useCallback(async (): Promise<PublishOutcome> => {
    if (!(await save())) return ref.current.conflict ? "conflict" : "failed";
    try {
      await publishDraft(id, ref.current.revision);
      dispatch({ type: "published" });
      clearLocalCopy(id);
      return "published";
    } catch (e) {
      const current = conflictOf(e);
      if (current) {
        dispatch({ type: "conflict", current });
        return "conflict";
      }
      const errors = fieldErrorsOf(e);
      if (errors.length) {
        dispatch({ type: "publishFailed", errors });
        return "invalid";
      }
      setLastError(e);
      return "failed";
    }
  }, [dispatch, id, save]);

  return { state, dispatch, save, publish, keepMine, takeTheirs, lastError };
}
