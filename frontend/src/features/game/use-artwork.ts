/**
 * Обложка или постер ответа раунда (`artwork`, ADR 0073) как прямая ссылка в `<img>`. Ссылка
 * короткоживущая (TTL 60 с), а итоги читают дольше, поэтому: ссылка уже истекла по часам клиента
 * или картинка не открылась — один раз запрашиваем свежую (`POST .../rounds/{i}/artwork`). Если и
 * она не открылась, возвращаем `null` — экран показывает заглушку, а не зацикливается.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, unwrap } from "@/shared/api/client";
import type { Artwork } from "@/shared/api/types";
import { msUntilExpiry } from "./use-final-clue";

export async function issueArtwork(sessionId: string, roundIndex: number): Promise<Artwork> {
  return unwrap<Artwork>(
    await api().game.POST("/v1/sessions/{session_id}/rounds/{index}/artwork", {
      params: { path: { session_id: sessionId, index: roundIndex } },
    }),
  );
}

type Source = { url: string; expires_at: string };

export function useRoundArtwork(
  sessionId: string,
  roundIndex: number,
  initial: Source | null | undefined,
): { url: string | null; onError: () => void } {
  const from = initial?.url ?? null;
  const expiresAt = initial?.expires_at ?? null;
  // Результат перевыдачи привязан к исходной ссылке: новая ссылка из bff начинает с нуля.
  const [fresh, setFresh] = useState<{ from: string; url: string | null } | null>(null);
  const refreshedFor = useRef<string | null>(null);

  // Запросить свежую ссылку: состояние меняется только в ответе, не в теле эффекта.
  const requestFresh = useCallback(() => {
    if (from === null) return;
    refreshedFor.current = from;
    issueArtwork(sessionId, roundIndex).then(
      (next) => setFresh({ from, url: next.url }),
      () => setFresh({ from, url: null }),
    );
  }, [from, sessionId, roundIndex]);

  // Ссылка истекла до показа (страница итогов открыта давно): сразу берём свежую.
  useEffect(() => {
    if (from !== null && expiresAt !== null && msUntilExpiry(expiresAt) <= 0) requestFresh();
  }, [from, expiresAt, requestFresh]);

  const onError = () => {
    if (from === null) return;
    // Свежая ссылка тоже не открылась: заглушка, без новой выдачи (иначе цикл).
    if (refreshedFor.current === from) setFresh({ from, url: null });
    else requestFresh();
  };

  const url = fresh && fresh.from === from ? fresh.url : from;
  return { url, onError };
}
