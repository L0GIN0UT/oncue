/**
 * Текст ошибки social по коду RFC 9457. Ключ — `social.error.<код без префикса сервиса>`;
 * вызывающий переводит через `useDynamicT` с запасным `social.error.generic`.
 */
import { isApiError, NetworkError } from "@/shared/api/errors";

/** Коды из описаний ручек social (проверка переводов — messages.test.ts). */
export const SOCIAL_ERROR_CODES = [
  "already_friends",
  "blocked",
  "blocked_by_you",
  "cannot_block_self",
  "chat_muted",
  "collection_not_found",
  "comment_edit_expired",
  "comment_rejected",
  "comments_rate_limit",
  "friend_requests_daily_limit",
  "friends_limit",
  "invite_forbidden",
  "player_not_found",
  "report_no_shared_match",
  "request_pending",
] as const;

/** Дополнительные ключи: коды чужих сервисов, у которых на экране social свой текст. */
export const SOCIAL_EXTRA_KEYS = [
  "generic",
  "network",
  "rate_limited",
  "guest_forbidden",
  "online_banned",
  "account_banned",
] as const;

export function socialErrorKey(e: unknown): string {
  if (e instanceof NetworkError) return "social.error.network";
  if (!isApiError(e)) return "social.error.generic";
  if (e.code === "auth.guest_forbidden") return "social.error.guest_forbidden";
  if (e.code === "match.online_banned") return "social.error.online_banned";
  if (e.code === "auth.account_banned") return "social.error.account_banned";
  if (e.code.startsWith("social.")) return `social.error.${e.code.slice(7)}`;
  if (e.status === 429) return "social.error.rate_limited";
  return "social.error.generic";
}
