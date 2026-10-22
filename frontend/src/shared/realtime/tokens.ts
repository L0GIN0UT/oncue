/**
 * Токены Centrifugo (D9, ADR 0035): connection — от identity (`POST /v1/realtime/token`),
 * subscription канала `room:{code}` — от match (`POST /v1/rooms/{code}/realtime-token`).
 * Оба — single-flight, как refresh сессии: переподключение и повторная подписка не
 * порождают пачку запросов. 401 доходит сюда только после неудачного refresh в `apiFetch`.
 */
import { api, unwrap } from "@/shared/api/client";
import type { RealtimeSubscription, RealtimeToken } from "@/shared/api/types";
import { singleFlight } from "./single-flight";

export const fetchConnectionToken = singleFlight(
  () => "connection",
  async (): Promise<string> =>
    unwrap<RealtimeToken>(await api().identity.POST("/v1/realtime/token")).token,
);

export const fetchSubscriptionToken = singleFlight(
  (code: string) => code,
  async (code: string): Promise<string> =>
    unwrap<RealtimeSubscription>(
      await api().match.POST("/v1/rooms/{code}/realtime-token", { params: { path: { code } } }),
    ).token,
);
