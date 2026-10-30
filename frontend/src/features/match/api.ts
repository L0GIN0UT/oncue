/** Чтение комнат и матчей match (`/api/match/v1/...`) и личности игрока (identity `/v1/me`). */
import { api, unwrap } from "@/shared/api/client";
import type { MyMatchesPage, RateKind, RoomState, RoomView, TickResult } from "@/shared/api/types";
import type { components } from "@/shared/api/schema/match";
import { newIdempotencyKey } from "@/shared/lib/id";

type CreateRoomRequest = components["schemas"]["CreateRoomRequest"];

export async function fetchRoomState(code: string): Promise<RoomState> {
  return unwrap(await api().match.GET("/v1/rooms/{code}/state", { params: { path: { code } } }));
}

export async function fetchRoom(code: string): Promise<RoomView> {
  return unwrap(await api().match.GET("/v1/rooms/{code}", { params: { path: { code } } }));
}

export async function sendTick(code: string, knownVersion: number | null): Promise<TickResult> {
  return unwrap(
    await api().match.POST("/v1/rooms/{code}/tick", {
      params: { path: { code } },
      body: { known_version: knownVersion },
    }),
  );
}

export async function fetchMyMatches(cursor?: string | null): Promise<MyMatchesPage> {
  return unwrap(
    await api().match.GET("/v1/me/matches", {
      params: { query: cursor ? { cursor } : {} },
    }),
  );
}

/** Похвала или жалоба на соперника (`PlayerRated`, D14). Повтор — 409 `match.already_rated`. */
export async function rateOpponent(matchId: string, toUserId: string, kind: RateKind) {
  const { error, response } = await api().match.POST("/v1/matches/{match_id}/rate", {
    params: {
      path: { match_id: matchId },
      header: { "Idempotency-Key": newIdempotencyKey() },
    },
    body: { to_user_id: toUserId, kind },
  });
  unwrap({ data: null, error, response });
}

/** Создать комнату по онлайн-шаблону; хост — я (слот 0). */
export async function createRoom(body: CreateRoomRequest, key = newIdempotencyKey()) {
  return unwrap<RoomView>(
    await api().match.POST("/v1/rooms", {
      params: { header: { "Idempotency-Key": key } },
      body,
    }),
  );
}

export { useMyUserId } from "@/features/auth/use-my-user-id";
