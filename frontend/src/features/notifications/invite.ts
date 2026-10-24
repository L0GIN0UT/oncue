/**
 * Приглашение в комнату (`room_invite`, этап 5c): машинное поле `data.room_code` ведёт в
 * `/room/<код>`. Код проверяется по формату комнат — произвольная строка в ссылку не попадает.
 */
import { isRoomCode, normalizeRoomCode } from "@/features/match/code";
import type { NotificationItem } from "@/shared/api/types";

export const ROOM_INVITE_TYPE = "room_invite";

export function roomInviteHref(item: Pick<NotificationItem, "type" | "data">): string | null {
  if (item.type !== ROOM_INVITE_TYPE) return null;
  const raw = item.data?.room_code;
  if (typeof raw !== "string") return null;
  const code = normalizeRoomCode(raw);
  return isRoomCode(code) ? `/room/${code}` : null;
}
