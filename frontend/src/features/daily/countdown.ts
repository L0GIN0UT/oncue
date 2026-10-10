/**
 * Отсчёт до смены дня. Момент смены приходит с сервером (`next_reset_at`, 00:00 в часовом поясе
 * источника дейлика, по умолчанию Europe/Moscow — D6). Локальный расчёт полуночи — только
 * запасной вариант, когда блок дейликов недоступен.
 */

export const DEFAULT_DAY_TIME_ZONE = "Europe/Moscow";

/** Смещение часового пояса от UTC (мс) в момент `at`. */
function zoneOffsetMs(at: Date, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(at);
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0);
  const asUtc = Date.UTC(
    get("year"),
    get("month") - 1,
    get("day"),
    get("hour"),
    get("minute"),
    get("second"),
  );
  return asUtc - Math.floor(at.getTime() / 1000) * 1000;
}

/** Дата `YYYY-MM-DD` игрового дня в часовом поясе `timeZone`. */
export function dayIn(at: Date, timeZone: string = DEFAULT_DAY_TIME_ZONE): string {
  return new Date(at.getTime() + zoneOffsetMs(at, timeZone)).toISOString().slice(0, 10);
}

/** Ближайшая полночь после `at` в часовом поясе `timeZone`. */
export function nextMidnight(at: Date, timeZone: string = DEFAULT_DAY_TIME_ZONE): Date {
  const local = new Date(at.getTime() + zoneOffsetMs(at, timeZone));
  const nextLocalMidnightUtc = Date.UTC(
    local.getUTCFullYear(),
    local.getUTCMonth(),
    local.getUTCDate() + 1,
  );
  const guess = new Date(nextLocalMidnightUtc - zoneOffsetMs(at, timeZone));
  // Переход на летнее время между `at` и полуночью меняет смещение — уточняем по нему.
  return new Date(nextLocalMidnightUtc - zoneOffsetMs(guess, timeZone));
}

export type Remaining = { hours: number; minutes: number; seconds: number; totalMs: number };

/** Сколько осталось до `target`; после наступления — нули. */
export function remainingUntil(target: Date, now: Date): Remaining {
  const totalMs = Math.max(0, target.getTime() - now.getTime());
  const total = Math.floor(totalMs / 1000);
  return {
    hours: Math.floor(total / 3600),
    minutes: Math.floor((total % 3600) / 60),
    seconds: total % 60,
    totalMs,
  };
}

/** `ЧЧ:ММ:СС` для табло отсчёта. */
export function formatClock(r: Remaining): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(r.hours)}:${pad(r.minutes)}:${pad(r.seconds)}`;
}
