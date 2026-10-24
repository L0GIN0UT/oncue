/**
 * Сезонные события (этап 5d, ADR 0054): чтение страницы — bff (`/v1/pages/events/{key}`),
 * засчёт находки-элемента — прямо в progression (ADR 0017; bff мутаций не имеет).
 */
import { api, unwrap } from "@/shared/api/client";
import type { ClaimResult } from "@/shared/api/progression-types";
import type { EventPageView } from "@/shared/api/types";

export const eventPageQueryKey = (key: string) => ["page", "event", key] as const;

/** Данные страницы события живут недолго: прогресс меняется от игры в других разделах. */
export const EVENT_STALE_MS = 30_000;

export async function fetchEventPage(key: string): Promise<EventPageView> {
  return unwrap(
    await api().web.GET("/v1/pages/events/{event_key}", {
      params: { path: { event_key: key } },
    }),
  );
}

/** Засчитать находку-элемент интерфейса. Идемпотентно: повтор с тем же ключом — тот же итог. */
export async function claimFind(
  eventKey: string,
  find: string,
  idempotencyKey: string,
): Promise<ClaimResult> {
  return unwrap(
    await api().progression.POST("/v1/events/{event_key}/finds/{find}/claim", {
      params: {
        path: { event_key: eventKey, find },
        header: { "Idempotency-Key": idempotencyKey },
      },
    }),
  );
}
