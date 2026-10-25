"use client";
/**
 * Данные события для клиентских компонентов. Баннер — блок `event` оболочки (bff
 * `/v1/pages/shell`, запрос общий с колокольчиком); страница события — только пользователю
 * (аноним и гость получают 401/403, поэтому без аккаунта запрос не делается).
 */
import { useQuery } from "@tanstack/react-query";
import type { EventBannerCard, EventPageView } from "@/shared/api/types";
import { useViewer } from "@/features/auth/session";
import { fetchShell, shellQueryKey } from "@/features/notifications/api";
import { EVENT_STALE_MS, eventPageQueryKey, fetchEventPage } from "./api";

/** Активное событие из оболочки; `null` — события нет или bff не ответил. */
export function useShellEvent(): EventBannerCard | null {
  const viewer = useViewer();
  const shell = useQuery({
    queryKey: shellQueryKey,
    queryFn: fetchShell,
    enabled: !viewer.pending,
    staleTime: 60_000,
    retry: false,
  });
  return shell.data?.event ?? null;
}

export function useEventPage(key: string | null | undefined) {
  const viewer = useViewer();
  return useQuery<EventPageView>({
    queryKey: eventPageQueryKey(key ?? ""),
    queryFn: () => fetchEventPage(key!),
    enabled: viewer.kind === "user" && Boolean(key),
    staleTime: EVENT_STALE_MS,
    retry: false,
  });
}
