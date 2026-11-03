"use client";
/**
 * Устойчивость блоков страниц bff: блок «недоступен» (`status: unavailable`) — сбой апстрима на
 * миг, а не состояние. Экран делает один повтор, а если и он не помог, показывает последние
 * известные данные блока вместо «недоступно».
 */
import { useEffect, useState } from "react";
import type { Block } from "./blocks";

/** Пауза перед единственным повтором при недоступном блоке, мс. */
export const RETRY_UNAVAILABLE_MS = 2_000;

type Blocks = Record<string, Block<unknown> | null | undefined>;

/**
 * Блок, погасший в новом ответе, но исправный в прошлом, остаётся прошлым. Только блоки из
 * `keys`: так не подставляются личные данные прежнего субъекта (после смены гостя на пользователя
 * и обратно), а только общие — режимы, каталог. Подставленный блок считается исправным, поэтому
 * повтор по нему не запускается, а затянувшийся сбой показывает устаревшие общие данные без
 * пометки — следующий успешный ответ (возврат на вкладку, обновление) их заменит.
 */
export function keepLastKnownBlocks<P extends { blocks: object }>(
  previous: P | null | undefined,
  next: P,
  keys: readonly string[],
): P {
  if (!previous) return next;
  const prev = previous.blocks as Blocks;
  const merged: Blocks = { ...(next.blocks as Blocks) };
  let changed = false;
  for (const key of keys) {
    if (merged[key]?.status === "unavailable" && prev[key]?.status === "ok") {
      merged[key] = prev[key];
      changed = true;
    }
  }
  return changed ? { ...next, blocks: merged as P["blocks"] } : next;
}

/** Есть ли в странице погасший блок (из `keys`, а без них — любой). */
export function hasUnavailableBlock(
  page: { blocks: object } | null | undefined,
  keys?: readonly string[],
): boolean {
  if (!page) return false;
  const blocks = page.blocks as Blocks;
  return (keys ?? Object.keys(blocks)).some((k) => blocks[k]?.status === "unavailable");
}

/**
 * Один повтор запроса страницы, если в ней есть погасший блок. Следующий сбой повтора не
 * получает: дальше работает возврат на вкладку и обычное обновление. Когда блоки целы,
 * счётчик сбрасывается — следующий сбой снова получит свой повтор.
 */
export function useRetryUnavailableOnce(
  page: { blocks: object } | null | undefined,
  refetch: () => unknown,
  keys?: readonly string[],
  delayMs: number = RETRY_UNAVAILABLE_MS,
): void {
  const broken = hasUnavailableBlock(page, keys);
  const [retries, setRetries] = useState(0);
  if (!broken && retries !== 0) setRetries(0);
  useEffect(() => {
    if (!broken || retries >= 1) return;
    const timer = setTimeout(() => {
      setRetries(1);
      void refetch();
    }, delayMs);
    return () => clearTimeout(timer);
  }, [broken, retries, refetch, delayMs]);
}
