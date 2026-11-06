"use client";
/**
 * Загадка уровня матча: ссылка приходит в дельте `level_opened` (одна на комнату, раунд и
 * уровень, D11), файл сразу скачивается в `Blob` и подаётся тем же реестром `clueRegistry`,
 * что в соло. Новой ссылки клиент не просит — её выдаёт только сервер при открытии уровня.
 */
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import type { LevelClue } from "@/shared/api/types";
import { AssetLoadError, downloadAsset } from "@/features/game/assets";
import type { LoadedClue } from "@/features/game/use-clue";

export const matchClueKey = (code: string, clue: LevelClue | null | undefined) =>
  ["match-clue", code, clue?.url ?? null] as const;

/** Файл уровня 1 следующего раунда, скачанный заранее в ревиле (ADR 0080): ключ в кэше. */
export const matchPreloadKey = (code: string, roundIndex: number) =>
  ["match-clue-preload", code, roundIndex] as const;

export async function loadLevelClue(clue: LevelClue, preloaded?: Blob): Promise<LoadedClue> {
  const blob = preloaded ?? (await downloadAsset(clue.url));
  return { ...clue, objectUrl: URL.createObjectURL(blob), blob };
}

/**
 * Скачать файл уровня 1 следующего раунда (`reveal.next_round_first_clue`), пока идёт ревил.
 * Ссылка раунд не открывает и таймер не запускает: настоящий уровень придёт дельтой
 * `level_opened` со своей ссылкой и возьмёт файл отсюда. Любая ошибка молча игнорируется -
 * тогда уровень грузится как раньше.
 */
export async function preloadLevelClue(
  client: QueryClient,
  code: string,
  roundIndex: number,
  clue: Pick<LevelClue, "url" | "level">,
): Promise<void> {
  if (clue.level !== 1) return;
  const key = matchPreloadKey(code, roundIndex);
  if (client.getQueryData(key) !== undefined) return;
  try {
    client.setQueryData(key, await downloadAsset(clue.url));
  } catch {
    /* предзагрузка необязательна */
  }
}

export function useLevelClue(
  code: string,
  clue: LevelClue | null | undefined,
  /** Раунд уровня: по нему берётся предзагруженный файл уровня 1 (только если он есть). */
  roundIndex?: number,
) {
  const client = useQueryClient();
  return useQuery({
    queryKey: matchClueKey(code, clue),
    queryFn: () => {
      if (clue!.level !== 1 || roundIndex === undefined) return loadLevelClue(clue!);
      const key = matchPreloadKey(code, roundIndex);
      const preloaded = client.getQueryData<Blob>(key);
      client.removeQueries({ queryKey: key });
      return loadLevelClue(clue!, preloaded);
    },
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
      client.removeQueries({ queryKey: ["match-clue-preload", code] });
    },
    [client, code],
  );
}
