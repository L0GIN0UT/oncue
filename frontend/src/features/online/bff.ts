/**
 * Экраны bff-web этапа 4a: `/v1/pages/online` (хаб) и `/v1/pages/match/{id}` (итог матча).
 *
 * `status` хаба: `ok`; `auth_required` — гость или аноним (D13, экран регистрации);
 * `session_expired` — токен не прошёл проверку (bff отвечает 200, поэтому общий refresh на
 * 401 в `apiFetch` не срабатывает): обновляем сессию через тот же single-flight и повторяем
 * запрос один раз; не вышло — экран входа.
 */
import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "@/shared/api/client";
import type { components } from "@/shared/api/schema/bff_web";
import { refreshSession } from "@/shared/api/refresh";
import { csrfToken } from "@/shared/lib/cookies";

type B = components["schemas"];
export type OnlinePage = B["OnlinePage"];
export type MatchPage = B["MatchPage"];
export type MatchSummary = B["MatchSummary"];
export type ItemShelvesCard = B["ItemShelvesCard"];

type Block<T> = { status: "ok" | "unavailable"; data?: T | null };

async function getOnline(): Promise<OnlinePage> {
  return unwrap(await api().web.GET("/v1/pages/online"));
}

/** Хаб с одним повтором после `session_expired` и успешного refresh. */
export async function fetchOnlinePage(
  get: () => Promise<OnlinePage> = getOnline,
  refresh: (csrf: string | null) => Promise<boolean> = refreshSession,
): Promise<OnlinePage> {
  const first = await get();
  if (first.status !== "session_expired") return first;
  if (!(await refresh(csrfToken()))) return first;
  return get();
}

export function useOnlinePage() {
  return useQuery({
    queryKey: ["page", "online"],
    queryFn: () => fetchOnlinePage(),
    staleTime: 10_000,
  });
}

export function useMatchPage(matchId: string | null) {
  return useQuery({
    queryKey: ["page", "match", matchId],
    queryFn: async (): Promise<MatchPage> =>
      unwrap(
        await api().web.GET("/v1/pages/match/{match_id}", {
          params: { path: { match_id: matchId! } },
        }),
      ),
    enabled: !!matchId,
    // Итог пишется транзакцией сразу после summary: короткая гонка — 404, повторяем.
    retry: (count, error) => count < 4 && (error as { status?: number }).status === 404,
    retryDelay: 700,
  });
}

/** Данные блока или `null` (блок погас — экран показывает заглушку). */
export function blockData<T>(block: Block<T> | null | undefined): T | null {
  return block?.status === "ok" ? (block.data ?? null) : null;
}
