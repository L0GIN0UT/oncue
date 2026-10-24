/**
 * Текст ошибки команды match по коду RFC 9457 (`match.*` из описаний ручек OpenAPI).
 * Ключ — `match.error.<код без префикса>`; у `match.chat_rejected` — по `details.reason`
 * (`denylist` | `link` | `too_long`). Неизвестный код — общий текст, сеть — свой.
 */
import { isApiError, NetworkError } from "@/shared/api/errors";

/** Коды из контракта match (для проверки переводов, см. messages.test.ts). */
export const MATCH_ERROR_CODES = [
  "active_room_conflict",
  "already_rated",
  "answer_kind_unsupported",
  "cannot_kick_self",
  "cannot_rate_self",
  "chat_disabled",
  "chat_muted",
  "chat_rejected",
  "collection_not_found",
  "collection_required",
  "cursor_invalid",
  "emote_unknown",
  "game_unavailable",
  "kicked",
  "ladder_not_found",
  "level_locked",
  "match_in_progress",
  "match_not_found",
  "mode_not_found",
  "mode_not_online",
  "not_all_ready",
  "not_enough_players",
  "not_in_queue",
  "not_host",
  "not_participant",
  "online_banned",
  "option_unknown",
  "phase_mismatch",
  "player_not_found",
  "players_over_limit",
  "pool_too_small",
  "queue_format_unavailable",
  "queue_matching",
  "ranked_forbidden",
  "rate_limited",
  "room_full",
  "room_not_found",
  "rooms_unavailable",
  "setting_not_allowed",
  "too_many_collections",
] as const;

export const CHAT_REJECT_REASONS = ["denylist", "link", "too_long"] as const;

/** Ключ i18n ошибки; вызывающий переводит через `useDynamicT` с запасным `match.error.generic`. */
export function matchErrorKey(e: unknown): string {
  if (e instanceof NetworkError) return "match.error.network";
  if (!isApiError(e)) return "match.error.generic";
  if (e.code === "auth.guest_forbidden") return "match.error.guest_forbidden";
  if (e.status === 429) return "match.error.rate_limited";
  if (e.code === "match.chat_rejected") {
    const reason = e.details?.reason;
    return typeof reason === "string" && (CHAT_REJECT_REASONS as readonly string[]).includes(reason)
      ? `match.error.chat_rejected_${reason}`
      : "match.error.chat_rejected";
  }
  return e.code.startsWith("match.") ? `match.error.${e.code.slice(6)}` : "match.error.generic";
}
