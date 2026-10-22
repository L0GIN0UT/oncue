"use client";
/**
 * Загадка уровня матча: ссылка приходит в дельте `level_opened` (одна на комнату, раунд и
 * уровень, D11), файл сразу скачивается в `Blob` и подаётся тем же реестром `clueRegistry`,
 * что в соло. Новой ссылки клиент не просит — её выдаёт только сервер при открытии уровня.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import type { LevelClue } from "@/shared/api/types";
import { AssetLoadError, downloadAsset } from "@/features/game/assets";
import type { LoadedClue } from "@/features/game/use-clue";

export const matchClueKey = (code: string, clue: LevelClue | null | undefined) =>
  ["match-clue", code, clue?.url ?? null] as const;

export async function loadLevelClue(clue: LevelClue): Promise<LoadedClue> {
  const blob = await downloadAsset(clue.url);
  return { ...clue, objectUrl: URL.createObjectURL(blob), blob };
}

export function useLevelClue(code: string, clue: LevelClue | null | undefined) {
  return useQuery({
    queryKey: matchClueKey(code, clue),
    queryFn: () => loadLevelClue(clue!),
    enabled: !!clue,
    staleTime: Infinity,
    gcTime: 5 * 60_000,
    retry: (count, error) => count < 1 && error instanceof AssetLoadError && error.status === null,
  });
}

/** Освободить `blob:`-адреса загадок комнаты при уходе с экрана. */
export function useReleaseLevelClues(code: string): void {
  const client = useQueryClient();
  useEffect(
    () => () => {
      for (const [, data] of client.getQueriesData<LoadedClue>({ queryKey: ["match-clue", code] }))
        if (data) URL.revokeObjectURL(data.objectUrl);
      client.removeQueries({ queryKey: ["match-clue", code] });
    },
    [client, code],
  );
}
