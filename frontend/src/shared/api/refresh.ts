/**
 * Обновление сессии (ротация refresh-токена) — single-flight внутри вкладки и между вкладками.
 *
 * Повторное использование refresh отзывает всю сессию (services/identity/CLAUDE.md), поэтому
 * две вкладки не должны обновлять её одновременно: вкладки сериализуются через Web Locks API,
 * а вкладка, дождавшаяся блокировки, сначала проверяет, не обновила ли сессию соседняя
 * (identity перевыдаёт `gv_csrf` при каждом refresh).
 */
import { CSRF_HEADER, csrfToken } from "../lib/cookies";
import { reportIfBanned } from "./banned";

export const REFRESH_PATH = "/api/identity/v1/auth/refresh";
const LOCK_NAME = "gv-auth-refresh";

type LockManagerLike = {
  request<T>(name: string, cb: () => Promise<T>): Promise<T>;
};

export type RefreshDeps = {
  fetch: typeof fetch;
  locks: LockManagerLike | null;
  readCsrf: () => string | null;
};

const defaultDeps = (): RefreshDeps => ({
  fetch: (...args) => globalThis.fetch(...args),
  locks: (globalThis.navigator as Navigator | undefined)?.locks ?? null,
  readCsrf: csrfToken,
});

let inflight: Promise<boolean> | null = null;

/**
 * Обновить сессию после 401 `auth.token_expired`.
 *
 * @param csrfAtRequest значение `gv_csrf` на момент неудачного запроса: если оно уже другое,
 *   сессию обновила другая вкладка и повторный refresh не нужен.
 * @returns удалось ли получить действующую сессию.
 */
export function refreshSession(
  csrfAtRequest: string | null,
  deps: RefreshDeps = defaultDeps(),
): Promise<boolean> {
  if (inflight) return inflight;
  const run = async (): Promise<boolean> => {
    const current = deps.readCsrf();
    if (current && current !== csrfAtRequest) return true;
    try {
      const res = await deps.fetch(REFRESH_PATH, {
        method: "POST",
        credentials: "same-origin",
        headers: current ? { [CSRF_HEADER]: current } : {},
      });
      // 403 `auth.account_banned`: аккаунт заблокирован при открытой сессии (ADR 0052).
      if (!res.ok) await reportIfBanned(res);
      return res.ok;
    } catch {
      return false;
    }
  };
  inflight = (deps.locks ? deps.locks.request(LOCK_NAME, run) : run()).finally(() => {
    inflight = null;
  });
  return inflight;
}
