/**
 * Ассет уровня: выдача ссылки (`POST .../rounds/{i}/clue`, TTL 60 с) и сразу скачивание в `Blob`,
 * чтобы повтор воспроизведения не требовал новой ссылки. Ошибка скачивания — повторная выдача
 * (не больше двух раз); ответы сервера 4xx не повторяются.
 */
import { useQuery, useQueryClient, type Query, type QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { api, unwrap } from "@/shared/api/client";
import { NetworkError } from "@/shared/api/errors";
import type { ClueOut } from "@/shared/api/types";
import { AssetLoadError, downloadAsset } from "./assets";

export { AssetLoadError };

/** Выданный ассет уровня: `blob:`-адрес для показа и сам `Blob` (текстовые загадки читают JSON). */
export type LoadedClue = ClueOut & { objectUrl: string; blob: Blob };

export const MAX_CLUE_RETRIES = 2;

export async function loadClue(
  sessionId: string,
  roundIndex: number,
  level: number,
  /** Файл, уже скачанный по `next_round_first_clue`: запрос выдачи остаётся (он стартует таймер). */
  preloaded?: Blob,
): Promise<LoadedClue> {
  const clue = unwrap<ClueOut>(
    await api().game.POST("/v1/sessions/{session_id}/rounds/{index}/clue", {
      params: { path: { session_id: sessionId, index: roundIndex } },
      body: { level },
    }),
  );
  const blob = preloaded ?? (await downloadAsset(clue.url));
  return { ...clue, objectUrl: URL.createObjectURL(blob), blob };
}

/** Предзагруженный файл уровня (`blob` без выдачи): ключ в кэше запросов. */
export const preloadKey = (sessionId: string, roundIndex: number, level: number) =>
  ["clue-preload", sessionId, roundIndex, level] as const;

/**
 * Скачать файл уровня 1 следующего раунда заранее (`next_round_first_clue`), пока открыт ответ
 * раунда. Ссылка раунд не открывает и таймер не запускает: следующий раунд стартует обычным
 * `POST .../clue`, который найдёт файл здесь и не будет качать его снова. Это лишь ускорение:
 * любая ошибка молча игнорируется — раунд тогда стартует как раньше, с обычным скачиванием.
 */
export async function preloadClue(
  client: QueryClient,
  sessionId: string,
  roundIndex: number,
  clue: Pick<ClueOut, "url" | "level">,
): Promise<void> {
  const key = preloadKey(sessionId, roundIndex, clue.level);
  if (client.getQueryData(key) !== undefined) return;
  try {
    client.setQueryData(key, await downloadAsset(clue.url));
  } catch {
    /* предзагрузка необязательна */
  }
}

export const clueKey = (sessionId: string, roundIndex: number, level: number) =>
  ["clue", sessionId, roundIndex, level] as const;

/**
 * Пока грузится следующий уровень того же раунда, остаётся прежний ассет: загадка не
 * размонтируется, высота страницы и прокрутка не прыгают. Чужой раунд или сессия — не наследуем.
 */
function keepWithinRound(sessionId: string, roundIndex: number | null) {
  return (previous: LoadedClue | undefined, previousQuery: Query<LoadedClue> | undefined) => {
    const key = previousQuery?.queryKey;
    return key && key[1] === sessionId && key[2] === roundIndex ? previous : undefined;
  };
}

export function useClue(sessionId: string, roundIndex: number | null, level: number | null) {
  const client = useQueryClient();
  return useQuery({
    placeholderData: keepWithinRound(sessionId, roundIndex),
    queryKey: clueKey(sessionId, roundIndex ?? -1, level ?? -1),
    queryFn: () => {
      // Файл мог быть скачан заранее; берём его один раз, выдача (таймер) всё равно идёт.
      const key = preloadKey(sessionId, roundIndex!, level!);
      const preloaded = client.getQueryData<Blob>(key);
      client.removeQueries({ queryKey: key });
      return loadClue(sessionId, roundIndex!, level!, preloaded);
    },
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
      client.removeQueries({ queryKey: ["clue-preload", sessionId] });
    },
    [client, sessionId],
  );
}
