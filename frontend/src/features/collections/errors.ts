/**
 * Ошибки сборников и медиатеки для показа: код (`collections.*`, причина `ugc.*`) → текст i18n,
 * путь (`title`, `items[3].custom.emoji`) → поле конструктора.
 */
import type { useTranslations } from "next-intl";
import { isApiError } from "@/shared/api/errors";

export type FieldError = {
  path: string;
  code: string;
  reason?: string;
  min?: number;
  max?: number;
};

export type FieldRef =
  | { kind: "title" | "description" | "tags" | "cover" | "mode" | "items" | "other" }
  | { kind: "item"; index: number; emoji: boolean };

const ITEM = /^items\[(\d+)\](\.custom\.emoji)?$/;

export function fieldOf(path: string): FieldRef {
  const item = ITEM.exec(path);
  if (item) return { kind: "item", index: Number(item[1]), emoji: Boolean(item[2]) };
  const head = path.split(/[.[]/)[0];
  switch (head) {
    case "title":
    case "description":
    case "items":
      return { kind: head };
    case "tags":
      return { kind: "tags" };
    case "cover":
      return { kind: "cover" };
    case "default_mode_key":
      return { kind: "mode" };
    default:
      return { kind: "other" };
  }
}

const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);

/** Ошибки полей из `details.errors` (422 `publish_invalid` / `draft_invalid`). */
export function fieldErrorsOf(e: unknown): FieldError[] {
  if (!isApiError(e)) return [];
  const raw = e.details?.errors;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((x): FieldError[] => {
    if (!x || typeof x !== "object") return [];
    const o = x as Record<string, unknown>;
    const path = str(o.path);
    const code = str(o.code);
    if (!path || !code) return [];
    return [{ path, code, reason: str(o.reason), min: num(o.min), max: num(o.max) }];
  });
}

type T = ReturnType<typeof useTranslations<never>>;
type Key = Parameters<T>[0];

/** Ключ i18n кода ошибки: `collections.draft_conflict` → `errors.collections.draft_conflict`. */
const CODE = /^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$/;
const codeKey = (code: string): Key | null =>
  CODE.test(code) ? (("errors." + code) as Key) : null;

/**
 * Текст ошибки по коду (`collections.*`, `library.*`, `game.*`, `auth.*`), уточнённый причиной
 * `ugc.*` (`details.reason`). Неизвестный код — общий текст.
 */
export function errorText(t: T, error: Pick<FieldError, "code" | "reason" | "min" | "max">) {
  const values = { min: error.min ?? 0, max: error.max ?? 0 };
  const key = codeKey(error.code);
  const base = key && t.has(key) ? t(key, values) : t("errors.unknown");
  const reason = error.reason ? codeKey(error.reason) : null;
  return reason && t.has(reason) ? `${base}: ${t(reason, values)}` : base;
}

/** Текст ошибки API целиком (без полей): код + `details.reason`/`max`. */
export function apiErrorText(t: T, e: unknown): string {
  if (!isApiError(e)) return t("errors.network");
  const details = e.details ?? {};
  return errorText(t, {
    code: e.code,
    reason: str(details.reason),
    min: num(details.min),
    max: num(details.max),
  });
}
