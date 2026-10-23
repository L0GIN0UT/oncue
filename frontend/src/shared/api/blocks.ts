/** Блоки страниц bff (`{status: ok | unavailable, data}`): общие хелперы экранов. */

export type Block<T> = { status: "ok" | "unavailable"; data?: T | null };

/** Данные блока или `null` (блок погас — экран показывает заглушку). */
export function blockData<T>(block: Block<T> | null | undefined): T | null {
  return block?.status === "ok" ? (block.data ?? null) : null;
}

/**
 * Блок отсутствует (`null`/не пришёл — поток C2 ещё не включён) — секцию не рисуем;
 * в отличие от `unavailable`, когда показываем «не удалось загрузить».
 */
export function hasBlock(block: Block<unknown> | null | undefined): boolean {
  return block != null;
}
