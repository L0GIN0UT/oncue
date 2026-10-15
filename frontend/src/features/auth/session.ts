/**
 * Сессия браузера. `gv_access` — HttpOnly, поэтому признак сессии — cookie `gv_csrf`
 * (identity выдаёт её вместе с access при входе, refresh и гостевой сессии).
 */
import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "@/shared/api/client";
import { isApiError } from "@/shared/api/errors";
import type { SessionInfo } from "@/shared/api/types";
import { refreshSession } from "@/shared/api/refresh";
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

/**
 * Начать гостевую сессию.
 *
 * 409 `auth.already_authenticated` — у identity есть живая сессия, но браузер, раз уж дошёл
 * сюда, не смог ею воспользоваться (например, исчезла cookie access). Это успех только после
 * успешного refresh; иначе ошибка уходит вызывающему (без бесконечного цикла 401 → 409).
 */
export async function startGuestSession(): Promise<void> {
  const result = await api().identity.POST("/v1/auth/guest");
  if (result.response.status === 409 && (await refreshSession(csrfToken()))) return;
  unwrap(result);
}

/** Перед стартом игры: без сессии — выдать гостевую (D5). */
export async function ensureSession(): Promise<void> {
  if (!csrfToken()) await startGuestSession();
}

/**
 * Восстановить сессию, которую сервер не принял: просроченный access при живой cookie
 * `gv_csrf` лечится refresh-ом, без cookie или при неудачном refresh выдаётся гостевая.
 *
 * Нужна там, где 401 до браузера не доходит: bff отвечает 200 и прячет отказ game в блоке
 * страницы (`unavailable`), поэтому общий refresh на 401 в `apiFetch` не срабатывает.
 */
export async function recoverSession(): Promise<void> {
  const csrf = csrfToken();
  if (csrf && (await refreshSession(csrf))) return;
  await startGuestSession();
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
