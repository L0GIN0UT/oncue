/** Страница лидербордов bff (`/v1/pages/leaderboards`): публичная; `around=me` — личная. */
import { api, unwrap } from "@/shared/api/client";
import type { LeaderboardsPage } from "@/shared/api/types";

export type LeaderboardsQuery = {
  ladder?: string | null;
  cursor?: string | null;
  around?: "me" | null;
};

export async function fetchLeaderboards(q: LeaderboardsQuery): Promise<LeaderboardsPage> {
  const query: Record<string, string> = {};
  if (q.ladder) query.ladder = q.ladder;
  if (q.cursor) query.cursor = q.cursor;
  if (q.around) query.around = q.around;
  return unwrap(await api().web.GET("/v1/pages/leaderboards", { params: { query } }));
}
