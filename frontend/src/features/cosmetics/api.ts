/**
 * Публичное оформление игроков комнаты: progression `GET /v1/appearance?user_ids=` (до 12 за
 * вызов, только пользователю с аккаунтом). Ошибка или отказ — оформления просто нет: ник
 * показывается без аватара и рамки, комната работает как раньше.
 */
import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "@/shared/api/client";
import { useViewer } from "@/features/auth/session";
import { toAppearanceLike, type AppearanceLike } from "./slots";

/** Предел progression на один вызов. */
export const MAX_APPEARANCE_IDS = 12;

export async function fetchPlayersAppearance(
  userIds: string[],
): Promise<Map<string, AppearanceLike>> {
  const ids = [...new Set(userIds)].slice(0, MAX_APPEARANCE_IDS);
  if (!ids.length) return new Map();
  const data = unwrap(
    await api().progression.GET("/v1/appearance", { params: { query: { user_ids: ids } } }),
  );
  return new Map(data.players.map((p) => [p.user_id, toAppearanceLike(p)]));
}

/** Оформление игроков по `user_id`; пока грузится или недоступно — пустая карта. */
export function usePlayersAppearance(userIds: string[]): Map<string, AppearanceLike> {
  const viewer = useViewer();
  const ids = [...new Set(userIds)].sort().slice(0, MAX_APPEARANCE_IDS);
  const query = useQuery({
    queryKey: ["appearance", ids.join(",")],
    queryFn: () => fetchPlayersAppearance(ids),
    enabled: viewer.kind === "user" && ids.length > 0,
    staleTime: 60_000,
    retry: false,
  });
  return query.data ?? EMPTY;
}

const EMPTY = new Map<string, AppearanceLike>();
