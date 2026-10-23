/** Очередь рейтинговой дуэли match (`/api/match/v1/queue/*`, ADR 0038). */
import { api, unwrap } from "@/shared/api/client";
import type { QueueFormat, QueueTicket } from "@/shared/api/types";
import { newIdempotencyKey } from "@/shared/lib/id";

export async function enterQueue(
  ladder: string,
  format: QueueFormat,
  key = newIdempotencyKey(),
): Promise<QueueTicket> {
  return unwrap(
    await api().match.POST("/v1/queue/enter", {
      params: { header: { "Idempotency-Key": key } },
      body: { ladder, format },
    }),
  );
}

/** Heartbeat очереди: продлевает запись и отдаёт состояние (404 `match.not_in_queue` — записи нет). */
export async function tickQueue(): Promise<QueueTicket> {
  return unwrap(await api().match.POST("/v1/queue/tick"));
}

export async function leaveQueue(key = newIdempotencyKey()): Promise<void> {
  const { error, response } = await api().match.POST("/v1/queue/leave", {
    params: { header: { "Idempotency-Key": key } },
  });
  unwrap({ data: null, error, response });
}
