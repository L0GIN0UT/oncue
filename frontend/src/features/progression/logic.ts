/** Чистая логика экранов прогрессии (без React): проценты, признаки, исход спина. */

/** Высший уровень: опыта до следующего нет. */
export function isMaxLevel(p: { xp_to_next: number | null }): boolean {
  return p.xp_to_next === null;
}

/** Заполнение полосы опыта, 0–100; на высшем уровне — 100. Не округляется вверх до 100 раньше времени. */
export function xpPercent(xpInLevel: number, xpToNext: number | null): number {
  if (xpToNext === null) return 100;
  const span = xpInLevel + xpToNext;
  if (span <= 0) return 0;
  return Math.min(99, Math.max(0, Math.floor((xpInLevel / span) * 100)));
}

/** Шанс сектора (доля 0–1) в процентах с одним знаком, без хвостовых нулей. */
export function chancePercent(chance: number): number {
  return Math.round(chance * 1000) / 10;
}

/** Когда наступит `target` относительно `now`: по календарным суткам в часовом поясе браузера. */
export function dayRelation(target: Date, now: Date): "today" | "tomorrow" | "later" {
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(target) - startOf(now)) / 86_400_000);
  if (days <= 0) return "today";
  return days === 1 ? "tomorrow" : "later";
}

/** Исход спина: свежий или повтор (сервер вернул тот же результат за сутки). */
export function spinOutcome(result: { repeated: boolean }): "new" | "repeated" {
  return result.repeated ? "repeated" : "new";
}
