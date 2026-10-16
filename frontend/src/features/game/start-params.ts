/**
 * Параметры старта из адреса `/play/{mode}` (разбираются и на сервере страницы, и в тестах):
 * сборник и число раундов этапа 3b (D4).
 */
import type { RoundOption } from "@/shared/api/types";

/** Сборник и число раундов (`5|10|25|"all"`) из адреса старта. */
export type CollectionStart = { collectionId: string; rounds: RoundOption | null };

const ROUNDS = new Set(["5", "10", "25"]);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Число раундов из адреса: только варианты контракта, иначе — выбор сервера (первый вариант). */
export function parseRounds(raw: string | null | undefined): RoundOption | null {
  if (raw === "all") return "all";
  return raw && ROUNDS.has(raw) ? (Number(raw) as RoundOption) : null;
}

/** Сборник из адреса: id сверяет game, здесь — только форма uuid (мусор не уходит в API). */
export function parseCollectionStart(
  collection: string | null | undefined,
  rounds: string | null | undefined,
): CollectionStart | null {
  if (!collection || !UUID.test(collection)) return null;
  return { collectionId: collection, rounds: parseRounds(rounds) };
}
