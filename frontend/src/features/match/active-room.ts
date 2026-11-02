/**
 * «Моя активная комната» — источник правды сервера (правило D10: одна комната на игрока).
 *
 * `GET /v1/rooms/active` отвечает кодом и фазой комнаты, в которой игрок состоит сейчас, или
 * пусто (вышел, комната закрыта, молчал дольше `presence_timeout_s`). По нему фронт при старте
 * предлагает вернуться, а 409 `active_room_conflict` превращает в диалог «выйти и перейти».
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { api, unwrap } from "@/shared/api/client";
import { isApiError } from "@/shared/api/errors";
import type { RoomPhase } from "@/shared/api/types";
import { useViewer } from "@/features/auth/session";
import { sendCommand, type CommandOptions } from "./commands";

export type ActiveRoom = { code: string; phase: RoomPhase };
export type RoomConflict = { code: string; phase: RoomPhase | null };

export const ACTIVE_ROOM_KEY = ["match", "active-room"] as const;

/** Идущий матч: «Выйти» из него засчитывается как сдача (нужно подтверждение). */
export function isLivePhase(phase: RoomPhase | null | undefined): boolean {
  return (
    phase === "countdown" ||
    phase === "level_open" ||
    phase === "level_closed" ||
    phase === "reveal" ||
    phase === "paused"
  );
}

/** Комната из 409 `match.active_room_conflict`; конфликт очереди (без `details.code`) — `null`. */
export function conflictOf(e: unknown): RoomConflict | null {
  if (!isApiError(e, "match.active_room_conflict")) return null;
  const code = e.details?.code;
  if (typeof code !== "string") return null;
  const phase = e.details?.phase;
  return { code, phase: typeof phase === "string" ? (phase as RoomPhase) : null };
}

export async function fetchActiveRoom(): Promise<ActiveRoom | null> {
  const body = unwrap(await api().match.GET("/v1/rooms/active"));
  return body.code && body.phase ? { code: body.code, phase: body.phase } : null;
}

/**
 * Выйти из комнаты из любой фазы. 403/404 (уже не участник, комнаты нет или она закрыта) —
 * тоже выход: сервер при этом освобождает указатель, ошибкой это быть не должно.
 */
export async function leaveRoom(code: string, options: CommandOptions = {}): Promise<void> {
  try {
    await sendCommand(code, "leave", {}, options);
  } catch (e) {
    if (isApiError(e) && (e.status === 403 || e.status === 404)) return;
    throw e;
  }
}

/** Активная комната игрока; для гостя и анонима запроса нет. Окно/вкладка в фокусе — свежий ответ. */
export function useActiveRoom(): { room: ActiveRoom | null; pending: boolean } {
  const viewer = useViewer();
  const query = useQuery({
    queryKey: ACTIVE_ROOM_KEY,
    queryFn: fetchActiveRoom,
    enabled: viewer.kind === "user",
    staleTime: 0,
    retry: false,
    refetchOnWindowFocus: true,
  });
  return { room: query.data ?? null, pending: query.isPending && viewer.kind === "user" };
}

/** Выйти из комнаты и сбросить локальное состояние: «активная комната», вход, снимок. */
export function useLeaveRoom(): (code: string) => Promise<void> {
  const client = useQueryClient();
  return useCallback(
    async (code: string) => {
      await leaveRoom(code);
      client.setQueryData(ACTIVE_ROOM_KEY, null);
      client.removeQueries({ queryKey: ["room-entry", code] });
      void client.invalidateQueries({ queryKey: ACTIVE_ROOM_KEY });
    },
    [client],
  );
}
