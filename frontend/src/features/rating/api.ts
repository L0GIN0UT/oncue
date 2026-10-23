/** Чтение rating (`/api/rating/v1/...`): изменения рейтинга матча для экрана итога. */
import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "@/shared/api/client";
import { pollMatchChanges } from "./logic";

import type { MatchChange, MatchChanges } from "@/shared/api/types";

export type { MatchChange, MatchChanges };

/** Итог ждёт обработки `MatchFinished` до 10 с: 10 попыток раз в секунду (D4 плана). */
export const CHANGES_POLL = { attempts: 10, delayMs: 1000 } as const;

export async function fetchMatchChanges(
  matchId: string,
  signal?: AbortSignal,
): Promise<MatchChanges> {
  return unwrap(
    await api().rating.GET("/v1/matches/{match_id}/changes", {
      params: { path: { match_id: matchId } },
      signal,
    }),
  );
}

/** Пауза, прерываемая сигналом отмены. */
const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });

/**
 * Изменения рейтинга матча. `data === null` — не дождались (экран пишет «обновится позже»);
 * ошибка не ломает итог — блок гаснет сам.
 */
export function useMatchChanges(matchId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["rating", "changes", matchId],
    queryFn: ({ signal }) =>
      pollMatchChanges((): Promise<MatchChanges> => fetchMatchChanges(matchId, signal), {
        ...CHANGES_POLL,
        wait: sleep,
        signal,
      }),
    enabled,
    retry: false,
    // Дождались — данные не меняются; не дождались (null) — при следующем показе опрашиваем снова.
    staleTime: (query) => (query.state.data == null ? 0 : Infinity),
  });
}
