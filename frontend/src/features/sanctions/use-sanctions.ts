"use client";
/**
 * Действующие санкции зрителя из блока `sanctions` оболочки (bff `/v1/pages/shell`); запрос
 * общий с колокольчиком (тот же ключ). Блок погас или не пришёл — ограничений «нет»: экран
 * не должен ломаться из-за второстепенного блока, а сами запреты проверяют сервисы.
 */
import { useQuery } from "@tanstack/react-query";
import { blockData } from "@/shared/api/blocks";
import type { SanctionKind, SanctionsCard } from "@/shared/api/types";
import { useViewer } from "@/features/auth/session";
import { fetchShell, shellQueryKey } from "@/features/notifications/api";

export function useSanctions(): SanctionsCard["items"] {
  const viewer = useViewer();
  const shell = useQuery({
    queryKey: shellQueryKey,
    queryFn: fetchShell,
    enabled: viewer.kind === "user",
    staleTime: 5 * 60_000,
    retry: false,
  });
  return blockData(shell.data?.sanctions)?.items ?? [];
}

export function useHasSanction(kind: SanctionKind): boolean {
  return useSanctions().some((s) => s.kind === kind);
}
