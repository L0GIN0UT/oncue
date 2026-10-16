/**
 * Режимы-шаблоны сборников (`<domain>.collection.<вид>`, source `collection`, D4).
 *
 * game не отдаёт шаблоны в `GET /v1/modes`, а `GET /v1/collections/{id}/modes` есть только у
 * опубликованного сборника. Поэтому конструктор берёт список из переводов `modes.<domain>.collection`:
 * их наличие для каждого сида `seeds/game/modes/*.collection.*` проверяет messages.test.ts.
 * Неизвестный режим черновика (сид без перевода) всё равно остаётся в списке.
 */
import { useMessages } from "next-intl";
import type { Domain } from "@/shared/api/types";

const COLLECTION_SOURCE = "collection";

export function collectionModeKeys(messages: unknown, domain: Domain): string[] {
  const modes = (messages as { modes?: Record<string, Record<string, unknown>> }).modes;
  const group = modes?.[domain]?.[COLLECTION_SOURCE];
  if (!group || typeof group !== "object") return [];
  return Object.keys(group).map((kind) => `${domain}.${COLLECTION_SOURCE}.${kind}`);
}

/** Режимы сборника домена; `current` (режим черновика) добавляется, если его нет в переводах. */
export function useCollectionModes(domain: Domain, current?: string | null): string[] {
  const keys = collectionModeKeys(useMessages(), domain);
  return current && !keys.includes(current) ? [...keys, current] : keys;
}

/** Ключ перевода названия режима по его ключу (`film.collection.frame` → `modes.film…title`). */
export const modeTitleKey = (modeKey: string) => `modes.${modeKey}.title`;
