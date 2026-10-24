/** Ошибки покупки и оформления → ключи сообщений `shop.error.*` (ru.json). */
import { ApiError, NetworkError } from "@/shared/api/errors";

export type PurchaseError =
  | "insufficientFunds"
  | "alreadyOwned"
  | "notAvailable"
  | "notFound"
  | "auth"
  | "rateLimited"
  | "unavailable"
  | "network"
  | "other";

export function purchaseErrorKey(e: unknown): PurchaseError {
  if (e instanceof NetworkError) return "network";
  if (!(e instanceof ApiError)) return "other";
  if (e.status === 401 || e.code === "auth.guest_forbidden") return "auth";
  if (e.status === 409 && e.code === "progression.insufficient_funds") return "insufficientFunds";
  if (e.status === 409 && e.code === "progression.already_owned") return "alreadyOwned";
  if (e.status === 403 && e.code === "progression.item_not_available") return "notAvailable";
  if (e.status === 404) return "notFound";
  if (e.status === 429) return "rateLimited";
  if (e.status >= 500) return "unavailable";
  return "other";
}

export type EquipError = "notOwned" | "slotMismatch" | "network" | "other";

export function equipErrorKey(e: unknown): EquipError {
  if (e instanceof NetworkError) return "network";
  if (!(e instanceof ApiError)) return "other";
  if (e.status === 404) return "notOwned";
  if (e.status === 422 && e.code === "progression.item_slot_mismatch") return "slotMismatch";
  return "other";
}
