/**
 * Локальная копия черновика в `localStorage` (D2): правки переживают закрытую вкладку и обрыв
 * сети. Хранилище может быть недоступно (приватный режим, квота, запрет cookie) — тогда копии
 * просто нет, конструктор работает с сервером.
 */
import type { DraftBody } from "@/shared/api/types";
import type { LocalCopy } from "./draft";

const PREFIX = "gv:collection-draft:";

function storage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

export function readLocalCopy(collectionId: string): LocalCopy | null {
  try {
    const raw = storage()?.getItem(PREFIX + collectionId);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    const { body, baseRevision } = parsed as { body?: DraftBody; baseRevision?: unknown };
    if (!body || typeof body.title !== "string" || typeof baseRevision !== "number") return null;
    return { body, baseRevision };
  } catch {
    return null;
  }
}

export function writeLocalCopy(collectionId: string, copy: LocalCopy): void {
  try {
    storage()?.setItem(PREFIX + collectionId, JSON.stringify(copy));
  } catch {
    // Квота или запрет хранилища: копия не обязательна.
  }
}

export function clearLocalCopy(collectionId: string): void {
  try {
    storage()?.removeItem(PREFIX + collectionId);
  } catch {
    // Хранилище недоступно — удалять нечего.
  }
}

/** Подпись устройства для экрана конфликта (`client_label`): браузер и система, без деталей. */
export function deviceLabel(ua: string = globalThis.navigator?.userAgent ?? ""): string {
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /OPR\//.test(ua)
      ? "Opera"
      : /Firefox\//.test(ua)
        ? "Firefox"
        : /Chrome\//.test(ua)
          ? "Chrome"
          : /Safari\//.test(ua)
            ? "Safari"
            : "Browser";
  const os = /Android/.test(ua)
    ? "Android"
    : /iPhone|iPad/.test(ua)
      ? "iOS"
      : /Windows/.test(ua)
        ? "Windows"
        : /Mac OS X/.test(ua)
          ? "macOS"
          : /Linux/.test(ua)
            ? "Linux"
            : "";
  return os ? `${browser} · ${os}` : browser;
}
