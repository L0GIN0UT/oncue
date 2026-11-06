"use client";
/**
 * Картинки обложек карточек сборников (ADR 0079): `cover_artwork` из bff или из пакетной ручки
 * `POST /v1/collections/covers` для списков, которые фронт берёт напрямую из collections.
 * Ссылка живёт от часа до двух: истекла по часам клиента или не открылась — один раз просим свежую
 * (та же ручка, один id); вторая неудача оставляет пресет, без цикла. Ошибки сети интерфейс не
 * показывает: у карточки остаётся пресет.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import type { CoverArtwork } from "@/shared/api/types";
import { msUntilExpiry } from "@/features/game/use-final-clue";
import { COVERS_BATCH_MAX, collectionKeys, fetchCollectionCovers } from "./api";

export type CoverMap = Record<string, CoverArtwork | null>;

/** Разбить id на уникальные пачки не больше `COVERS_BATCH_MAX`. */
function chunks(ids: string[]): string[][] {
  const unique = [...new Set(ids)];
  const out: string[][] = [];
  for (let i = 0; i < unique.length; i += COVERS_BATCH_MAX)
    out.push(unique.slice(i, i + COVERS_BATCH_MAX));
  return out;
}

/**
 * Обложки видимых карточек, одним запросом на группу (страницу списка). Группы кэшируются
 * отдельно: подгрузка следующей страницы не перезапрашивает уже полученные. Ошибка запроса —
 * пустой результат (пресет), без повторов.
 */
export function useCollectionCovers(groups: string[][]): CoverMap {
  const batches = groups.flatMap(chunks);
  return useQueries({
    queries: batches.map((ids) => ({
      queryKey: collectionKeys.covers(ids),
      queryFn: ({ signal }: { signal: AbortSignal }) => fetchCollectionCovers(ids, signal),
      retry: false,
      // Ссылка действует от часа: обновлять раньше не нужно, истечение ловит перевыдача.
      staleTime: 30 * 60_000,
      refetchOnWindowFocus: false,
    })),
    combine: (results) => Object.assign({}, ...results.map((r) => r.data ?? {})) as CoverMap,
  });
}

type Source = { url: string; expires_at: string };

/**
 * Ссылка обложки одной карточки с перевыдачей. `initial` — то, что пришло со страницей (или из
 * пачки); `null`/`undefined` — картинки нет, показываем пресет.
 */
export function useCollectionCoverArtwork(
  collectionId: string,
  initial: Source | null | undefined,
): { url: string | null; onError: () => void } {
  const from = initial?.url ?? null;
  const expiresAt = initial?.expires_at ?? null;
  // Результат перевыдачи привязан к исходной ссылке: новая ссылка начинает с нуля.
  const [fresh, setFresh] = useState<{ from: string; url: string | null } | null>(null);
  const refreshedFor = useRef<string | null>(null);

  const requestFresh = useCallback(() => {
    if (from === null) return;
    refreshedFor.current = from;
    fetchCollectionCovers([collectionId]).then(
      (map) => setFresh({ from, url: map[collectionId]?.url ?? null }),
      () => setFresh({ from, url: null }),
    );
  }, [from, collectionId]);

  // Ссылка уже истекла по часам клиента: сразу одна свежая, без ошибки картинки.
  useEffect(() => {
    if (from !== null && expiresAt !== null && msUntilExpiry(expiresAt) <= 0) requestFresh();
  }, [from, expiresAt, requestFresh]);

  const onError = () => {
    if (from === null) return;
    // Свежая ссылка тоже не открылась: пресет, без новой выдачи.
    if (refreshedFor.current === from) setFresh({ from, url: null });
    else requestFresh();
  };

  const url = fresh && fresh.from === from ? fresh.url : from;
  return { url, onError };
}
