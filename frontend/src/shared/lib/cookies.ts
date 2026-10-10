/** Чтение cookie, доступных JS (только `gv_csrf`; `gv_access` и `gv_refresh` — HttpOnly). */
export const CSRF_COOKIE = "gv_csrf";
export const CSRF_HEADER = "X-CSRF-Token";

export function readCookie(
  name: string,
  source: string = globalThis.document?.cookie ?? "",
): string | null {
  for (const part of source.split(";")) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    if (part.slice(0, eq).trim() === name) return decodeURIComponent(part.slice(eq + 1).trim());
  }
  return null;
}

export function csrfToken(): string | null {
  return readCookie(CSRF_COOKIE);
}
