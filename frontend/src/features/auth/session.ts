/**
 * Сессия браузера. `gv_access` — HttpOnly, поэтому признак сессии — cookie `gv_csrf`
 * (identity выдаёт её вместе с access при входе, refresh и гостевой сессии).
 */
import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "@/shared/api/client";
import { isApiError } from "@/shared/api/errors";
import type { SessionInfo } from "@/shared/api/types";
import { csrfToken } from "@/shared/lib/cookies";

export const sessionQueryKey = ["session"] as const;

/** Кто играет: пользователь, гость или никто (bff `GET /v1/session`). */
export function useSessionInfo() {
  return useQuery({
    queryKey: sessionQueryKey,
    queryFn: async (): Promise<SessionInfo> => unwrap(await api().web.GET("/v1/session")),
    staleTime: 60_000,
  });
}

/** Начать гостевую сессию; уже действующая сессия (409) — тоже успех. */
export async function startGuestSession(): Promise<void> {
  const result = await api().identity.POST("/v1/auth/guest");
  if (result.response.status === 409) return;
  unwrap(result);
}

/** Перед стартом игры: без сессии — выдать гостевую (D5). */
export async function ensureSession(): Promise<void> {
  if (!csrfToken()) await startGuestSession();
}

/**
 * Выполнить действие, требующее сессии. Если сессия оказалась недействительной (401 после
 * неудачного refresh), выдаётся гостевая и действие повторяется один раз.
 */
export async function withSession<T>(action: () => Promise<T>): Promise<T> {
  await ensureSession();
  try {
    return await action();
  } catch (e) {
    if (!isApiError(e) || e.status !== 401) throw e;
    await startGuestSession();
    return action();
  }
}
