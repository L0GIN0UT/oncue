/**
 * Чистая логика рейтинга для экранов 4b: сезон, прогресс до звания, знак дельты и опрос
 * `GET /matches/{id}/changes` (итог ждёт, пока rating применит `MatchFinished`).
 */
import { isApiError } from "@/shared/api/errors";

const DAY_MS = 86_400_000;

/** Сколько дней осталось до `ends_at` (неполный день — день); после конца — 0. */
export function daysLeft(endsAt: string, nowMs: number): number {
  const end = Date.parse(endsAt);
  if (Number.isNaN(end)) return 0;
  return Math.max(0, Math.ceil((end - nowMs) / DAY_MS));
}

/** Очков до порога следующего звания; `null` — высшее звание или калибровка. */
export function pointsToNext(
  rating: number | null | undefined,
  nextTierAt: number | null | undefined,
): number | null {
  if (rating == null || nextTierAt == null) return null;
  return Math.max(0, nextTierAt - rating);
}

/** Дельта со знаком: «+18», «−7» (типографский минус), «0». */
export function signedDelta(delta: number): string {
  if (delta > 0) return `+${delta}`;
  if (delta < 0) return `−${Math.abs(delta)}`;
  return "0";
}

export type PollOptions = {
  attempts: number;
  delayMs: number;
  wait: (ms: number, signal?: AbortSignal) => Promise<void>;
  /** Отмена (размонтирование, смена матча): опрос прекращается, результат — `null`. */
  signal?: AbortSignal;
};

/**
 * Опрашивает ручку, пока она отвечает 404 (событие ещё не обработано). Попытки исчерпаны —
 * `null`: экран итога показывает «рейтинг обновится позже», не ломаясь. Остальные ошибки
 * пробрасываются.
 */
export async function pollMatchChanges<T>(
  fetchOnce: () => Promise<T>,
  { attempts, delayMs, wait, signal }: PollOptions,
): Promise<T | null> {
  for (let i = 0; i < attempts; i++) {
    if (signal?.aborted) return null;
    try {
      return await fetchOnce();
    } catch (e) {
      if (!isApiError(e) || e.status !== 404) throw e;
    }
    if (i < attempts - 1) await wait(delayMs, signal);
  }
  return null;
}

export type BoardErrorKind = "notFound" | "login" | "staleCursor" | "invalidLadder" | "failed";

/**
 * Ошибка страницы лидербордов: 404 — раздела нет, 401 — `around=me` без входа, 422 с курсором —
 * устаревший чужой курсор, 422 без курсора — некорректный раздел.
 */
export function classifyBoardError(error: unknown, hadCursor: boolean): BoardErrorKind {
  if (!isApiError(error)) return "failed";
  if (error.status === 404) return "notFound";
  if (error.status === 401) return "login";
  if (error.status === 422) return hadCursor ? "staleCursor" : "invalidLadder";
  return "failed";
}
