/**
 * Ассет уровня: выдача ссылки (`POST .../rounds/{i}/clue`, TTL 60 с) и сразу скачивание в `Blob`,
 * чтобы повтор воспроизведения не требовал новой ссылки. Ошибка скачивания — повторная выдача
 * (не больше двух раз); ответы сервера 4xx не повторяются.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { api, unwrap } from "@/shared/api/client";
import { NetworkError } from "@/shared/api/errors";
import type { ClueOut } from "@/shared/api/types";

export type LoadedClue = ClueOut & { objectUrl: string };

/** Ссылка выдана, но файл не скачался (истекла, обрыв): нужна новая выдача. */
export class AssetLoadError extends Error {
  constructor(readonly status: number | null) {
    super(`asset load failed: ${status ?? "network"}`);
    this.name = "AssetLoadError";
  }
}

export const MAX_CLUE_RETRIES = 2;

export async function loadClue(
  sessionId: string,
  roundIndex: number,
  level: number,
): Promise<LoadedClue> {
  const clue = unwrap<ClueOut>(
    await api().game.POST("/v1/sessions/{session_id}/rounds/{index}/clue", {
      params: { path: { session_id: sessionId, index: roundIndex } },
      body: { level },
    }),
  );
  let res: Response;
  try {
    res = await fetch(new URL(clue.url, globalThis.location.origin), {
      credentials: "omit",
      cache: "no-store",
    });
  } catch {
    throw new AssetLoadError(null);
  }
  if (!res.ok) throw new AssetLoadError(res.status);
  const blob = await res.blob();
  return { ...clue, objectUrl: URL.createObjectURL(blob) };
}

export const clueKey = (sessionId: string, roundIndex: number, level: number) =>
  ["clue", sessionId, roundIndex, level] as const;

export function useClue(sessionId: string, roundIndex: number | null, level: number | null) {
  return useQuery({
    queryKey: clueKey(sessionId, roundIndex ?? -1, level ?? -1),
    queryFn: () => loadClue(sessionId, roundIndex!, level!),
    enabled: roundIndex !== null && level !== null,
    staleTime: Infinity,
    gcTime: 15 * 60_000,
    retry: (count, error) =>
      count < MAX_CLUE_RETRIES &&
      (error instanceof AssetLoadError || error instanceof NetworkError),
    retryDelay: 300,
  });
}

/** Освободить `blob:`-адреса ассетов сессии при уходе с экрана. */
export function useReleaseClues(sessionId: string): void {
  const client = useQueryClient();
  useEffect(
    () => () => {
      for (const [, data] of client.getQueriesData<LoadedClue>({ queryKey: ["clue", sessionId] })) {
        if (data) URL.revokeObjectURL(data.objectUrl);
      }
      client.removeQueries({ queryKey: ["clue", sessionId] });
    },
    [client, sessionId],
  );
}
